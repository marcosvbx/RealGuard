"""Rotas de sistema: páginas, vídeo, status, presenças e Telegram."""

import os
import subprocess
import threading
import time

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from api.security import SESSION_COOKIE, audit, require, require_any, session_user
from app.engine import engine
from config.settings import WEB_DIR
from database import access, repository

router = APIRouter()

VIDEO_SESSION_CHECK_SEC = 30


def _page(name: str) -> FileResponse:
    # no-store: ao editar os arquivos de web/ basta recarregar a página.
    response = FileResponse(os.path.join(WEB_DIR, name), headers={"Cache-Control": "no-store"})
    from config.ui_locale import current
    response.set_cookie("rg_ui_default", current(), samesite="lax", httponly=False)
    return response


@router.get("/", include_in_schema=False)
def dashboard_page(request: Request):
    user = session_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/login?trocar=1", status_code=303)
    return _page("index.html")


_FONTS = {"archivo.woff2", "poppins-300.woff2", "poppins-500.woff2", "poppins-600.woff2", "poppins-700.woff2"}


@router.get("/fonts/{name}", include_in_schema=False)
def font_file(name: str):
    """Fonte da marca, servida localmente (funciona na rede isolada da AIBOX, sem Google Fonts)."""
    if name not in _FONTS:
        raise HTTPException(status_code=404, detail="Fonte não encontrada.")
    return FileResponse(os.path.join(WEB_DIR, "fonts", name), media_type="font/woff2",
                        headers={"Cache-Control": "public, max-age=604800"})


@router.get("/ui/i18n.js", include_in_schema=False)
def ui_i18n_script():
    return FileResponse(os.path.join(WEB_DIR, "i18n.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})


@router.get("/api/ui/locale", include_in_schema=False)
def ui_default_locale():
    from config.ui_locale import current
    return {"locale": current()}


@router.get("/preconfig", include_in_schema=False)
def preconfig_page(user=Depends(require("system.configure"))):
    return _page("preconfig.html")


@router.get("/api/system/preconfig")
def get_preconfig(user=Depends(require("system.configure"))):
    from config.preconfig import read
    return read()


@router.get("/api/system/process")
def process_identity(user=Depends(require("system.configure"))):
    return {"pid": os.getpid()}


@router.post("/api/system/preconfig/open-native")
def open_native_preconfig(request: Request, user=Depends(require("system.configure"))):
    """No notebook local, abre a mesma janela nativa usada ao iniciar o executável."""
    host = (request.url.hostname or "").lower()
    local = request.client and request.client.host in ("127.0.0.1", "::1")
    if os.name != "nt" or not local or host not in ("127.0.0.1", "localhost", "::1"):
        return {"opened": False}
    executable = os.path.join(os.path.dirname(WEB_DIR), "RealGuard.exe")
    if not os.path.isfile(executable):
        return {"opened": False}
    try:
        subprocess.Popen([executable, "--configure"], cwd=os.path.dirname(WEB_DIR), close_fds=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Não foi possível abrir a configuração: {exc}") from exc
    audit(request, user, "configuracao_aberta", "sistema")
    return {"opened": True}


@router.post("/api/system/preconfig/test/{target}")
def test_preconfig(target: str, payload: dict = Body(...), user=Depends(require("system.configure"))):
    from config.preconfig import test_connection
    try:
        return test_connection(payload, target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/preconfig")
def save_preconfig(request: Request, payload: dict = Body(...), user=Depends(require("system.configure"))):
    from config.preconfig import save
    from app.lifecycle import request as request_lifecycle
    try:
        changes = save(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(request, user, "preconfiguracao_alterada", "sistema", detail=(
        f"modo={changes['REALGUARD_START_MODE']}; idioma={changes['REALGUARD_UI_LOCALE']}; "
        f"camera={changes['CAMERA_MODE']}; proxy={'sim' if changes['HTTPS_PROXY'] else 'não'}"))
    timer = threading.Timer(0.8, request_lifecycle, args=("restart",))
    timer.daemon = True
    timer.start()
    return {"status": "restarting"}


@router.post("/api/system/stop")
def stop_realguard(request: Request, user=Depends(require("system.stop"))):
    from app.lifecycle import request as request_lifecycle
    audit(request, user, "realguard_encerrado", "sistema")
    timer = threading.Timer(0.8, request_lifecycle, args=("stop",))
    timer.daemon = True
    timer.start()
    return {"status": "stopping"}


@router.get("/login", include_in_schema=False)
def login_page():
    return _page("login.html")


@router.get("/video")
def video(request: Request, user=Depends(require_any("live.view", "students.edit"))):
    """MJPEG. A sessão é reconferida a cada 30 s: sair/desativar o usuário corta o vídeo."""
    token = request.cookies.get(SESSION_COOKIE, "")

    def stream():
        checked = time.time()
        for chunk in engine.mjpeg_stream():
            if time.time() - checked > VIDEO_SESSION_CHECK_SEC:
                checked = time.time()
                if access.get_session_user(token) is None:
                    return
            yield chunk

    return StreamingResponse(stream(), media_type="multipart/x-mixed-replace;boundary=frame",
                             headers={"Cache-Control": "no-store"})


@router.get("/video/{camera_id}")
def camera_video(camera_id: int, request: Request, user=Depends(require("live.view"))):
    """Vídeo de uma câmera cadastrada (Monitores com várias câmeras). Só a ativa tem IA."""
    from api.routes.cameras import video_stream
    token = request.cookies.get(SESSION_COOKIE, "")
    state = {"checked": time.time(), "ok": True}

    def alive() -> bool:
        if time.time() - state["checked"] > VIDEO_SESSION_CHECK_SEC:
            state["checked"] = time.time()
            state["ok"] = access.get_session_user(token) is not None
        return state["ok"]

    source = video_stream(camera_id, alive)

    def stream():
        for chunk in source:
            if not alive():
                return
            yield chunk

    return StreamingResponse(stream(), media_type="multipart/x-mixed-replace;boundary=frame",
                             headers={"Cache-Control": "no-store"})


@router.get("/api/status")
def status(user=Depends(require())):
    return engine.status()


@router.get("/api/presences")
def presences(limit: int = Query(100, ge=1, le=1000), user=Depends(require("overview.view"))):
    return {
        "hoje": repository.get_dashboard_stats().get("presences_today", 0),
        "registros": repository.list_presences(limit),
    }


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"••••{digits[-4:]}" if len(digits) >= 4 else ""


@router.get("/api/telegram/chats")
def telegram_chats(user=Depends(require("telegram.contacts"))):
    """
    Quem já conversou com o bot (registrado pelo próprio bot). Serve de
    alternativa manual; o normal é o vínculo automático pelo convite/número.
    O telefone sai mascarado: só os 4 últimos dígitos, para conferência.
    """
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram.")
    chats = repository.list_telegram_chats()
    for c in chats:
        c["phone"] = _mask_phone(c.get("phone", ""))
    return chats


@router.post("/api/mode")
def set_mode(request: Request, mode: str = Form(...), user=Depends(require("live.mode"))):
    """
    Função da câmera ativa: AUTO (entrada/saída decidida por aluno), ENTRY ou EXIT.
    Fica gravada no cadastro da câmera (vale também depois de reiniciar).
    """
    mode = mode.strip().upper()
    if mode not in repository.CAMERA_DIRECTIONS:
        raise HTTPException(status_code=400, detail="Modo inválido: use AUTO, ENTRY ou EXIT.")
    previous = engine.mode
    engine.set_mode(mode)
    repository.set_camera_direction(engine.settings.camera_id, mode)
    if previous != mode:
        audit(request, user, "modo_camera", "camera", engine.settings.camera_id, f"{previous} -> {mode}")
    return {"modo": mode}
