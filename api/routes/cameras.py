"""
Câmeras: cadastro, teste de conexão e troca da câmera ativa.

Pensado para o dia da banca: dá para cadastrar uma câmera IP (RTSP) ou a
webcam, testar pelo dashboard (com prévia da imagem) e ativá-la na hora,
sem editar arquivo nem reiniciar. A IA roda em UMA câmera por vez (a CPU da AIBOX
não comporta duas análises); as outras mostram só o vídeo nos Monitores
(app/camera/previews.py), e trocar a câmera da IA é um clique.

A senha RTSP nunca vai para o banco: fica no config/realguard.env (permissão 600) como
CAMERA_PASS_<id>, para a câmera abrir sem digitar de novo. A câmera padrão usa CAMERA_PASS.
"Manter após reiniciar" grava a câmera como padrão no realguard.env.
"""

import base64
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.security import audit, require
from app.camera import diagnose, sources
from app.camera.previews import previews
from app.engine import engine
from config import settings as settings_module
from config import setup_wizard
from database import repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cameras", tags=["cameras"])

HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")
USER_RE = re.compile(r"^[^:@/\s]{0,64}$")
TEST_TIMEOUT_SEC = 10.0
_test_lock = threading.Lock()


class CameraPayload(BaseModel):
    name: str
    location: str = ""
    source_kind: str = "rtsp"          # rtsp | webcam
    webcam_index: int = 0
    host: str = ""
    port: int = 554
    path: str = setup_wizard.DEFAULT_RTSP_PATH
    username: str = ""
    direction: str = "AUTO"            # função: AUTO (entrada/saída por aluno) | ENTRY | EXIT
    password: Optional[str] = None     # None/vazio = manter a guardada; texto = guardar no realguard.env


class TestPayload(CameraPayload):
    name: str = "Teste"
    password: str = ""
    camera_id: Optional[int] = None    # câmera cadastrada (para reaproveitar a senha do .env)


class ActivatePayload(BaseModel):
    password: str = ""
    persist: bool = False              # gravar como câmera padrão no realguard.env


def _clean(p: CameraPayload) -> Dict[str, Any]:
    kind = (p.source_kind or "").strip().lower()
    if kind not in ("rtsp", "webcam"):
        raise HTTPException(status_code=400, detail="Tipo de câmera inválido: use rtsp ou webcam.")
    name = (p.name or "").strip()
    if not 2 <= len(name) <= 60:
        raise HTTPException(status_code=400, detail="Dê um nome de 2 a 60 caracteres à câmera.")
    direction = (p.direction or "AUTO").strip().upper()
    if direction not in repository.CAMERA_DIRECTIONS:
        raise HTTPException(status_code=400, detail="Função da câmera inválida: AUTO, ENTRY ou EXIT.")
    data = {"name": name, "location": (p.location or "").strip()[:80], "source_kind": kind,
            "webcam_index": 0, "host": "", "port": 554, "path": "", "username": "", "direction": direction}
    if kind == "webcam":
        if not 0 <= int(p.webcam_index) <= 9:
            raise HTTPException(status_code=400, detail="Índice da webcam: de 0 a 9.")
        data["webcam_index"] = int(p.webcam_index)
        return data
    host = (p.host or "").strip()
    if not HOST_RE.match(host):
        raise HTTPException(status_code=400, detail="Informe o IP (ou nome) da câmera, ex.: 192.168.50.108.")
    if not 1 <= int(p.port) <= 65535:
        raise HTTPException(status_code=400, detail="Porta inválida.")
    path = (p.path or setup_wizard.DEFAULT_RTSP_PATH).strip()
    if not path.startswith("/") or " " in path or len(path) > 200:
        raise HTTPException(status_code=400, detail="Caminho RTSP inválido: deve começar com /.")
    user = (p.username or "").strip()
    if not USER_RE.match(user):
        raise HTTPException(status_code=400, detail="Usuário da câmera inválido.")
    data.update(host=host, port=int(p.port), path=path, username=user)
    return data


def _env_password(data: Dict[str, Any]) -> str:
    """Senha guardada: a desta câmera (CAMERA_PASS_<id>) ou a da câmera padrão (mesmo IP e usuário)."""
    return sources.stored_password(data)


def _source(data: Dict[str, Any], password: str) -> str:
    if data["source_kind"] == "webcam":
        return str(data["webcam_index"])
    return setup_wizard.build_rtsp_url(data["username"], password or _env_password(data),
                                       data["host"], str(data["port"]), data["path"])


def _keep_password(camera_id: int, data: Dict[str, Any], password: Optional[str]) -> None:
    if data["source_kind"] == "rtsp" and password:
        sources.save_password(camera_id, password)


def _probe(source: str, timeout: float = TEST_TIMEOUT_SEC) -> Dict[str, Any]:
    """Abre a fonte, lê 1 quadro e devolve prévia JPEG — com limite de tempo (o OpenCV pode travar)."""
    result: Dict[str, Any] = {"ok": False}

    def run():
        import cv2
        start = time.time()
        cap = None
        try:
            cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
            ok, frame = (cap.read() if cap.isOpened() else (False, None))
            if ok and frame is not None:
                h, w = frame.shape[:2]
                scale = min(1.0, 640.0 / max(1, w))
                small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
                okj, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
                result.update(ok=True, largura=w, altura=h, ms=int((time.time() - start) * 1000),
                              previa="data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode() if okj else "")
        except Exception as e:
            result["erro"] = str(e)
        finally:
            if cap is not None:
                cap.release()

    t = threading.Thread(target=run, daemon=True, name="teste-camera")
    t.start()
    t.join(timeout)
    if t.is_alive():
        result["erro"] = f"sem resposta em {int(timeout)} s"
    return result


def _active_snapshot() -> Dict[str, Any]:
    import cv2
    frame = engine.snapshot()
    if frame is None:
        return {"ok": False, "erro": "a câmera ativa não está enviando imagem"}
    h, w = frame.shape[:2]
    okj, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return {"ok": True, "largura": w, "altura": h, "ms": 0, "ativa": True,
            "previa": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode() if okj else ""}


def _get_or_404(camera_id: int) -> Dict[str, Any]:
    cam = repository.get_camera(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="Câmera não encontrada.")
    return cam


# ------------------------------------------------------------------ rotas
@router.get("")
def list_cameras(user=Depends(require("cameras.view"))):
    status = engine.status()
    active = engine.settings.camera_id
    cams = repository.list_cameras()
    for c in cams:
        c["ativa"] = c["id"] == active
        c["video"] = c["ativa"] or sources.can_stream(c)       # aparece nos Monitores
        prev = None if c["ativa"] else previews.status(c["id"])
        c["online"] = bool(status.get("camera_online")) if c["ativa"] else (prev["transmitindo"] or None)
        c["fps"] = status.get("fps_camera") if c["ativa"] else prev.get("fps_camera")
        c["senha_no_env"] = bool(c["source_kind"] == "rtsp" and _env_password(c))
    return {"ativa": active, "estado": status.get("estado"), "cameras": cams}


@router.post("")
def create_camera(payload: CameraPayload, request: Request, user=Depends(require("cameras.manage"))):
    data = _clean(payload)
    camera_id = repository.create_camera(data)
    _keep_password(camera_id, data, payload.password)
    audit(request, user, "camera_cadastrada", "camera", camera_id, f"{data['name']} ({data['source_kind']})"
          + (" · senha guardada" if data["source_kind"] == "rtsp" and payload.password else ""))
    return repository.get_camera(camera_id)


_SOURCE_FIELDS = ("source_kind", "webcam_index", "host", "port", "path", "username")


def _source_changed(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    return any(str(old.get(k) or "") != str(new.get(k) or "") for k in _SOURCE_FIELDS)


def _default_camera_id() -> int:
    try:
        return int(os.getenv("CAMERA_ID", "1") or 1)
    except ValueError:
        return 1


def _persist_default(cam: Dict[str, Any], password: str) -> None:
    """Grava a câmera no realguard.env: é nela que o sistema liga da próxima vez (com a IA)."""
    old_id = _default_camera_id()
    if old_id != cam["id"]:
        # A câmera padrão anterior usava o CAMERA_PASS, que vai ser sobrescrito: guarda a senha dela
        # como CAMERA_PASS_<id>, senão ela deixa de abrir (Monitores, "Usar esta câmera").
        old = repository.get_camera(old_id)
        old_password = sources.stored_password(old) if old and old["source_kind"] == "rtsp" else ""
        if old_password and not os.getenv(sources.password_key(old_id)):
            sources.save_password(old_id, old_password)
    updates = {"CAMERA_ID": str(cam["id"]), "CAMERA_NAME": cam["name"], "RTSP_URL": ""}
    if cam["source_kind"] == "webcam":
        updates.update(CAMERA_MODE="webcam", CAMERA_INDEX=str(cam["webcam_index"]))
    else:
        updates.update(CAMERA_MODE="rtsp", CAMERA_IP=cam["host"], CAMERA_PORT=str(cam["port"]),
                       CAMERA_RTSP_PATH=cam["path"], CAMERA_USER=cam["username"], CAMERA_PASS=password)
    setup_wizard.update_env_file(settings_module.ENV_FILE, updates,
                                 example_path=os.path.join(settings_module.CONFIG_DIR, "realguard.env.example"))
    for k, v in updates.items():    # o processo atual também passa a enxergar (ex.: senha do .env)
        os.environ[k] = v


@router.put("/{camera_id}")
def update_camera(camera_id: int, payload: CameraPayload, request: Request, user=Depends(require("cameras.manage"))):
    old = _get_or_404(camera_id)
    old_password = sources.stored_password(old) if old["source_kind"] == "rtsp" else ""
    data = _clean(payload)
    repository.update_camera(camera_id, data)
    _keep_password(camera_id, data, payload.password)
    previews.stop(camera_id)                 # reabre o vídeo com os dados novos
    moved = False
    if camera_id == engine.settings.camera_id:
        engine.settings.camera_name = data["name"]
        engine.set_mode(data["direction"])   # a função da câmera ativa vale na hora
        if _source_changed(old, data):
            # Câmera ativa trocou de endereço/caminho (ex.: outro modelo): a IA vai junto, na hora.
            cam = repository.get_camera(camera_id)
            password = payload.password or sources.stored_password(cam) or old_password
            if camera_id == _default_camera_id():
                _persist_default(cam, password)      # e continua nela ao reiniciar
            engine.switch_camera(cam["id"], cam["name"], sources.source_for(cam, password), cam["source_kind"])
            moved = True
    audit(request, user, "camera_alterada", "camera", camera_id,
          data["name"] + (" · IA reiniciada com o novo endereço" if moved else ""))
    return dict(repository.get_camera(camera_id), ia_reiniciada=moved)


@router.delete("/{camera_id}")
def delete_camera(camera_id: int, request: Request, user=Depends(require("cameras.manage"))):
    cam = _get_or_404(camera_id)
    if camera_id == engine.settings.camera_id:
        raise HTTPException(status_code=400, detail="Esta é a câmera ativa. Ative outra antes de excluir.")
    repository.delete_camera(camera_id)
    previews.stop(camera_id)
    sources.forget_password(camera_id)
    audit(request, user, "camera_excluida", "camera", camera_id, cam["name"])
    return {"status": "success"}


@router.post("/test")
def test_camera(payload: TestPayload, request: Request, user=Depends(require("cameras.manage"))):
    """Testa a conexão e devolve uma prévia. Não muda nada no sistema."""
    data = _clean(payload)
    saved = repository.get_camera(payload.camera_id) if payload.camera_id is not None else None
    if saved is not None and payload.camera_id == engine.settings.camera_id \
            and data["source_kind"] == engine.settings.camera_mode and not _source_changed(saved, data):
        result = _active_snapshot()    # a câmera já em uso (abrir de novo a webcam falharia)
    else:
        if not _test_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Já existe um teste de câmera em andamento.")
        password = payload.password or (sources.stored_password(dict(data, id=payload.camera_id))
                                        if payload.camera_id is not None else _env_password(data))
        try:
            if payload.camera_id is not None:
                previews.stop(payload.camera_id)     # webcam não abre duas vezes; RTSP: libera a conexão
            result = _probe(_source(data, password))
        finally:
            _test_lock.release()
        if not result["ok"] and data["source_kind"] == "webcam":
            result["dica"] = diagnose.webcam(data["webcam_index"]) or (
                "Se a webcam já está em uso pelo RealGuard, ative-a direto ou teste outro índice."
                if engine.settings.camera_mode == "webcam" else "Confira se a webcam está ligada e livre.")
        elif not result["ok"] and data["source_kind"] == "rtsp":
            code, msg = diagnose.rtsp(data["host"], data["port"], data["path"], data["username"], password)
            result["motivo"], result["dica"] = code, msg
        if result["ok"] and payload.camera_id is not None and payload.password:
            _keep_password(payload.camera_id, data, payload.password)    # testou e deu certo: guarda
            result["senha_guardada"] = True
    audit(request, user, "camera_testada", "camera", payload.camera_id or "",
          f"{data['source_kind']} {data['host'] or data['webcam_index']}: {'ok' if result['ok'] else 'falhou'}")
    return result


@router.post("/{camera_id}/activate")
def activate_camera(camera_id: int, payload: ActivatePayload, request: Request,
                    user=Depends(require("cameras.manage"))):
    cam = _get_or_404(camera_id)
    password = payload.password or _env_password(cam)
    if cam["source_kind"] == "rtsp" and not sources.is_env_camera(cam) and not password and cam["username"]:
        raise HTTPException(status_code=400, detail="Informe a senha da câmera para ativá-la.")
    if payload.password:
        _keep_password(cam["id"], cam, payload.password)
    source = sources.source_for(cam, password)
    previews.stop(cam["id"])                 # a câmera passa para a IA: fecha o vídeo extra dela
    engine.switch_camera(cam["id"], cam["name"], source, cam["source_kind"])
    engine.set_mode(cam.get("direction") or "AUTO")
    if payload.persist:
        _persist_default(cam, password or sources.stored_password(cam))
    audit(request, user, "camera_ativada", "camera", cam["id"],
          f"{cam['name']} ({cam['source_kind']})" + (" · gravada como padrão" if payload.persist else ""))
    return {"status": "success", "message": f"Ativando {cam['name']}… o vídeo volta em alguns segundos."
            + (" Ela continua ativa após reiniciar." if payload.persist else " Ao reiniciar, volta a câmera padrão.")}


# ------------------------------------------------------------------ vídeo de cada câmera
def video_stream(camera_id: int, alive):
    """MJPEG da câmera: a ativa vem da IA (com marcações); as outras, só o vídeo (sob demanda)."""
    if camera_id == engine.settings.camera_id:
        return engine.mjpeg_stream()
    cam = repository.get_camera(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="Câmera não encontrada.")
    if not sources.can_stream(cam):
        raise HTTPException(status_code=409, detail="Guarde a senha desta câmera (Ao vivo → câmera → Senha) "
                                                    "para ver o vídeo dela.")
    return previews.mjpeg(cam["id"], cam["name"], sources.source_for(cam), alive)
