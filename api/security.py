"""
Autenticação e autorização da API.

* Sessão por cookie HttpOnly (`rg_session`); o banco guarda só o hash do token.
* Toda rota (menos login e a página de login) depende de `current_user`,
  direta ou indiretamente por `require(...)`. Um teste percorre todas as
  rotas para garantir que nenhuma fique aberta por esquecimento.
* Proteção CSRF: requisições que alteram algo (POST/PUT/PATCH/DELETE) em
  /api precisam do cabeçalho `X-RealGuard: 1`, que um site de terceiros não
  consegue enviar (o navegador bloqueia cabeçalho próprio entre origens).
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from database import access

logger = logging.getLogger(__name__)

SESSION_COOKIE = "rg_session"
CSRF_HEADER = "x-realguard"
PUBLIC_PATHS = frozenset({"/login", "/api/auth/login", "/api/auth/state", "/api/ui/locale", "/ui/i18n.js", "/favicon.ico"})


def session_hours() -> float:
    try:
        return max(0.25, float(os.getenv("SESSION_HOURS", "12")))
    except ValueError:
        return 12.0


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def is_https(request: Request) -> bool:
    return request.url.scheme == "https"


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=int(session_hours() * 3600), httponly=True,
                        samesite="lax", secure=is_https(request), path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def session_user(request: Request) -> Optional[Dict[str, Any]]:
    """Usuário da sessão atual, ou None (não lança erro — usado por páginas)."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    try:
        return access.get_session_user(token)
    except Exception as e:
        logger.error(f"Falha ao validar sessão: {e}")
        return None


def current_user(request: Request) -> Dict[str, Any]:
    user = session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre novamente.")
    request.state.user = user
    return user


def require(*permissions: str) -> Callable[..., Dict[str, Any]]:
    """Dependência: usuário logado, sem senha provisória pendente e com TODAS as permissões."""
    def dependency(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if user.get("must_change_password"):
            raise HTTPException(status_code=403, detail="Troque sua senha provisória antes de continuar.")
        missing = [p for p in permissions if p not in user.get("permissions", [])]
        if missing:
            raise HTTPException(status_code=403, detail="Seu perfil não tem permissão para esta ação.")
        return user
    dependency.__name__ = "require_" + "_".join(p.replace(".", "_") for p in permissions) if permissions \
        else "require_login"
    return dependency


def require_any(*permissions: str) -> Callable[..., Dict[str, Any]]:
    """Dependência: usuário logado com PELO MENOS UMA das permissões."""
    def dependency(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if user.get("must_change_password"):
            raise HTTPException(status_code=403, detail="Troque sua senha provisória antes de continuar.")
        if not any(p in user.get("permissions", []) for p in permissions):
            raise HTTPException(status_code=403, detail="Seu perfil não tem permissão para esta ação.")
        return user
    dependency.__name__ = "require_any_" + "_".join(p.replace(".", "_") for p in permissions)
    return dependency


def has(user: Dict[str, Any], permission: str) -> bool:
    from app.arena import enabled
    if enabled() and permission.startswith("guardians."):
        return False
    return permission in (user or {}).get("permissions", [])


def audit(request: Optional[Request], user: Optional[Dict[str, Any]], action: str, target_type: str = "",
          target_id: Any = "", detail: str = "", username: str = "") -> None:
    access.log_audit(action, user=user, target_type=target_type, target_id=target_id, detail=detail,
                     ip=client_ip(request) if request is not None else "", username=username)


# ---------------------------------------------------------------- tentativas de login
class LoginThrottle:
    """
    Freia tentativa de senha por força bruta: após MAX_FAILS erros em WINDOW
    segundos (por usuário e por IP), bloqueia por LOCK segundos. Em memória:
    reiniciar o sistema zera — suficiente para um equipamento na rede local.
    """
    MAX_FAILS = 5
    WINDOW = 600
    LOCK = 300

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: Dict[str, list] = {}
        self._blocked: Dict[str, float] = {}

    def _keys(self, username: str, ip: str) -> Tuple[str, str]:
        return "u:" + access.normalize_username(username), "ip:" + ip

    def blocked_for(self, username: str, ip: str) -> int:
        now = time.time()
        with self._lock:
            return max([int(self._blocked.get(k, 0) - now) for k in self._keys(username, ip)] + [0])

    def failure(self, username: str, ip: str) -> None:
        now = time.time()
        with self._lock:
            for k in self._keys(username, ip):
                recent = [t for t in self._fails.get(k, []) if now - t < self.WINDOW] + [now]
                self._fails[k] = recent
                if len(recent) >= self.MAX_FAILS:
                    self._blocked[k] = now + self.LOCK
                    self._fails[k] = []

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            for k in self._keys(username, ip):
                self._fails.pop(k, None)
                self._blocked.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._blocked.clear()


throttle = LoginThrottle()


# ---------------------------------------------------------------- middleware ASGI
class SecurityMiddleware:
    """
    Cabeçalhos de segurança em toda resposta + checagem CSRF. ASGI "puro"
    (não BaseHTTPMiddleware) para não atrapalhar o stream MJPEG do /video.
    """
    UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"same-origin"),
        (b"permissions-policy", b"geolocation=(), microphone=()"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        from app.arena import allowed
        if not allowed(path, scope.get('method', 'GET')):
            from fastapi.responses import JSONResponse
            return await JSONResponse({'detail':'Recurso fora da apresentação Arena.'},status_code=403)(scope,receive,send)
        if scope.get("method") in self.UNSAFE and path.startswith("/api/"):
            headers = dict(scope.get("headers") or [])
            if headers.get(CSRF_HEADER.encode()) != b"1":
                body = b'{"detail":"Requisicao recusada (protecao CSRF). Recarregue a pagina."}'
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())] + self.HEADERS})
                await send({"type": "http.response.body", "body": body})
                return
        is_api = path.startswith("/api/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in headers}
                headers += [h for h in self.HEADERS if h[0] not in existing]
                if is_api and b"cache-control" not in existing:
                    headers.append((b"cache-control", b"no-store"))
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, send_with_headers)
