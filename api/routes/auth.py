"""Login, sessão e senha do próprio usuário."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.security import (
    audit, clear_session_cookie, client_ip, current_user, is_https, session_hours,
    set_session_cookie, throttle, SESSION_COOKIE,
)
from app.security.permissions import catalog
from database import access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


class PasswordPayload(BaseModel):
    current_password: str
    new_password: str


def _me(user) -> dict:
    from app.arena import enabled
    return {
        "arena": enabled(),
        "id": user["id"], "usuario": user["username"], "nome": user.get("full_name") or user["username"],
        "perfil": {"chave": user["role"], "nome": user.get("role_name") or user["role"]},
        "permissoes": user.get("permissions", []),
        "trocar_senha": bool(user.get("must_change_password")),
    }


@router.get("/state")
def auth_state(request: Request):
    """Público: a tela de login precisa saber se ainda não existe nenhum administrador."""
    try:
        needs_admin = access.count_users(active_admins_only=True) == 0
    except Exception:
        needs_admin = True
    return {"precisa_admin": needs_admin, "https": is_https(request)}


@router.post("/login")
def login(payload: LoginPayload, request: Request):
    ip = client_ip(request)
    username = access.normalize_username(payload.username)[:64]
    wait = throttle.blocked_for(username, ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Muitas tentativas. Tente de novo em {max(1, wait // 60)} min.")
    user = access.authenticate(username, payload.password)
    if user is None:
        throttle.failure(username, ip)
        audit(request, None, "login_falhou", "usuario", "", username=username)
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos.")
    throttle.success(username, ip)
    token = access.create_session(user["id"], session_hours(), ip, request.headers.get("user-agent", ""))
    audit(request, user, "login", "usuario", user["id"])
    full = access.get_session_user(token)
    response = JSONResponse(_me(full))
    set_session_cookie(response, token, request)
    return response


@router.post("/logout")
def logout(request: Request, user=Depends(current_user)):
    access.delete_session(request.cookies.get(SESSION_COOKIE, ""))
    audit(request, user, "logout", "usuario", user["id"])
    response = JSONResponse({"status": "success"})
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(user=Depends(current_user)):
    return _me(user)


@router.get("/permissions")
def permission_catalog(user=Depends(current_user)):
    return catalog()


@router.post("/password")
def change_password(payload: PasswordPayload, request: Request, user=Depends(current_user)):
    """Troca a própria senha (também usada para sair da senha provisória)."""
    if access.authenticate(user["username"], payload.current_password) is None:
        audit(request, user, "senha_troca_falhou", "usuario", user["id"])
        raise HTTPException(status_code=400, detail="A senha atual não confere.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="A nova senha precisa ser diferente da atual.")
    try:
        access.set_password(user["id"], payload.new_password, must_change=False)
    except access.AccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Encerra as outras sessões do usuário (ex.: um computador esquecido logado).
    access.delete_user_sessions(user["id"], except_token=request.cookies.get(SESSION_COOKIE, ""))
    audit(request, user, "senha_trocada", "usuario", user["id"])
    return {"status": "success", "message": "Senha alterada."}


# ---------------------------------------------------------------- Minha conta → Vincular Telegram
def _bot_username(block: bool = True):
    from app.engine import engine
    n = engine.security_notifier       # o vínculo da conta é com o bot da SEGURANÇA
    return n.get_bot_username(block=block) if n.enabled else None


@router.get("/telegram")
def my_telegram(user=Depends(current_user)):
    st = access.telegram_status(user["id"])
    from app.engine import engine
    perms = user.get("permissions", [])
    return dict(st, bot=_bot_username(block=False), configurado=engine.security_notifier.enabled,
                pode_ver="events.view" in perms, pode_decidir="events.decide" in perms)


class MyTelegramPayload(BaseModel):
    alertas: bool


@router.put("/telegram")
def my_telegram_prefs(payload: MyTelegramPayload, request: Request, user=Depends(current_user)):
    """Liga/desliga os alertas de segurança no próprio Telegram (só vale com a conta vinculada)."""
    from app.security_alerts import request_panel_update
    access.set_telegram_alerts(user["id"], payload.alertas)
    audit(request, user, "telegram_alertas_" + ("ligados" if payload.alertas else "desligados"), "usuario", user["id"])
    request_panel_update()      # manda os cartões de hoje (ou apaga os dele)
    return dict(access.telegram_status(user["id"]),
                message="Você vai receber os alertas no Telegram." if payload.alertas
                else "Alertas desligados no seu Telegram.")


@router.post("/telegram")
def link_my_telegram(request: Request, user=Depends(current_user)):
    """Link de uso único (15 min): abrir no celular e tocar em Iniciar liga este Telegram à conta."""
    from app.engine import engine
    if not engine.security_notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram ainda não foi configurado (Escola → Integrações).")
    bot = _bot_username()
    if not bot:
        raise HTTPException(status_code=503, detail="Não consegui falar com o Telegram agora (sem internet?).")
    code = access.create_telegram_code(user["id"])
    audit(request, user, "telegram_conta_link_gerado", "usuario", user["id"])
    return {"link": f"https://t.me/{bot}?start={code}", "bot": bot, "expira_min": access.TELEGRAM_CODE_MINUTES}


@router.delete("/telegram")
def unlink_my_telegram(request: Request, user=Depends(current_user)):
    old = access.unlink_telegram_user(user["id"])
    if old:
        import threading
        from app.engine import engine
        bot = engine.linker.security_bot
        if bot is not None:
            threading.Thread(target=bot.set_user_commands, args=(old, False), daemon=True).start()
    from app.security_alerts import request_panel_update
    request_panel_update()          # apaga os cartões e a lista fixada que estavam no Telegram dele
    audit(request, user, "telegram_conta_desvinculada", "usuario", user["id"])
    return {"vinculado": False, "message": "Telegram desvinculado da sua conta."}


@router.get("/qr")
def qr_code(u: str, user=Depends(current_user)):
    """QR Code (PNG) de um link do Telegram — para apontar o celular em vez de copiar o link."""
    from fastapi.responses import Response
    if not u.startswith("https://t.me/") or len(u) > 200:
        raise HTTPException(status_code=400, detail="Só links do Telegram.")
    try:
        import cv2
        qr = cv2.QRCodeEncoder_create().encode(u)
        img = cv2.resize(qr, (qr.shape[1] * 8, qr.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
        img = cv2.copyMakeBorder(img, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
        ok, buf = cv2.imencode(".png", img)
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=501, detail="QR Code indisponível neste equipamento.")
    return Response(buf.tobytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=600"})
