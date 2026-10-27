"""
Painel de monitores (janela própria, estilo CFTV).

A IA roda em UMA câmera por vez (a ativa). As outras câmeras cadastradas com
senha guardada também transmitem vídeo, sem IA (app/camera/previews.py): o painel
monta a grade com todas.
"""

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from api.routes.system import _page
from api.security import require, session_user
from app import system_metrics
from app.engine import STATE_RUNNING, STATE_STARTING, engine
from database import repository

router = APIRouter()


@router.get("/monitores", include_in_schema=False)
def monitors_page(request: Request):
    user = session_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.get("must_change_password"):
        return RedirectResponse("/login?trocar=1", status_code=303)
    return _page("monitores.html")


def _camera_state(status: dict) -> str:
    if engine.state == STATE_STARTING:
        return "iniciando"
    if engine.state != STATE_RUNNING:
        return "erro"
    if status.get("camera_online"):
        return "online"
    return "reconectando"


@router.get("/api/monitores")
def monitors(user=Depends(require("live.view"))):
    status = engine.status()
    active = engine.settings.camera_id
    try:
        cams = repository.list_cameras()
    except Exception:
        cams = []
    if not any(c["id"] == active for c in cams):     # banco ainda subindo: mostra ao menos a ativa
        cams.insert(0, {"id": active, "name": engine.settings.camera_name, "location": "", "fonte": "",
                        "source_kind": engine.settings.camera_mode, "direction": engine.mode})
    out = []
    for c in cams:
        is_active = c["id"] == active
        item = {
            "id": c["id"], "nome": c.get("name") or f"Câmera {c['id']}", "local": c.get("location") or "",
            "tipo": "webcam" if c.get("source_kind") == "webcam" else "rtsp",
            "fonte": c.get("fonte") if c.get("source_kind") == "webcam" or c.get("host") else "definida no realguard.env",
            "direcao": (engine.mode if is_active else c.get("direction")) or "AUTO",
            "ativa": is_active, "ficticia": bool(c.get("ficticio")),
            "estado": _camera_state(status) if is_active else "inativa",
        }
        if not is_active:
            from app.camera import sources
            from app.camera.previews import previews
            item["video"] = not c.get("ficticio") and sources.can_stream(c)
            if item["video"]:
                prev = previews.status(c["id"])
                item.update(estado=prev["estado"], transmitindo=prev["transmitindo"],
                            fps_camera=prev.get("fps_camera"), resolucao=prev.get("resolucao"),
                            fps_video=prev.get("fps_video"), resolucao_video=prev.get("resolucao_video"))
            elif not c.get("ficticio"):
                item["estado"] = "sem_senha"
        if is_active:
            item["video"] = True
            item.update({
                "transmitindo": item["estado"] == "online",
                "fps_camera": status.get("fps_camera"),
                "fps_processamento": status.get("fps_processamento"),
                "fps_video": status.get("fps_video"),
                "resolucao_video": status.get("resolucao_video"),
                "resolucao": status.get("resolucao"),
                "ultimo_quadro_s": status.get("ultimo_quadro_s"),
                "pessoas": status.get("pessoas", 0),
                "reconhecidos": status.get("reconhecidos", 0),
                "desconhecidos": status.get("desconhecidos", 0),
                "obstruidos": status.get("obstruidos", 0),
                "espelhado": bool(status.get("espelhado")),
            })
        out.append(item)
    out.sort(key=lambda x: (not x["ativa"], x["id"]))
    result = {
        "agora": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ativa": active,
        "estado": engine.state,
        "erro": engine.error,
        "aviso": engine.notice,
        "monitores": out,
        "pode": {"modo": "live.mode" in user.get("permissions", []),
                 "cameras": "cameras.manage" in user.get("permissions", []),
                 "diagnostico": "monitors.diagnostics" in user.get("permissions", [])},
    }
    if result["pode"]["diagnostico"]:
        result["sistema"] = {
            **system_metrics.snapshot(),
            "ligado_s": round(time.time() - engine.started_at) if engine.started_at else None,
            "ms_deteccao": status.get("ms_deteccao"),
            "ms_reconhecimento": status.get("ms_reconhecimento"),
            "fila_reconhecimento": status.get("fila_reconhecimento"),
            "reconhecimento_facial": bool(status.get("reconhecimento_facial")),
            "rostos_na_base": status.get("rostos_na_base"),
        }
    return result
