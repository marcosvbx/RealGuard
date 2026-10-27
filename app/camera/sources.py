"""
De onde vem o vídeo de cada câmera cadastrada (e a senha dela).

A senha RTSP nunca vai para o banco. Para a segunda câmera funcionar sem digitar a senha toda
vez (ativar, mostrar nos Monitores), ela fica no config/realguard.env como CAMERA_PASS_<id>
(arquivo com permissão 600, fora do Git). A câmera padrão continua usando CAMERA_PASS.
"""

import os
from typing import Any, Dict, Optional

from config import settings as settings_module
from config import setup_wizard


def password_key(camera_id: int) -> str:
    return f"CAMERA_PASS_{int(camera_id)}"


def _env_default_password(cam: Dict[str, Any]) -> str:
    """Senha do realguard.env (CAMERA_PASS), só se for a MESMA câmera configurada lá (mesmo IP e usuário)."""
    if (cam.get("host") or "") == os.getenv("CAMERA_IP", "").strip() and \
            (cam.get("username") or "") == os.getenv("CAMERA_USER", "").strip():
        return os.getenv("CAMERA_PASS", "")
    return ""


def stored_password(cam: Dict[str, Any]) -> str:
    if cam.get("id") is not None:
        own = os.getenv(password_key(cam["id"]), "")
        if own:
            return own
    return _env_default_password(cam)


def has_stored_password(cam: Dict[str, Any]) -> bool:
    return bool(stored_password(cam))


def save_password(camera_id: int, password: str) -> None:
    key = password_key(camera_id)
    setup_wizard.update_env_file(settings_module.ENV_FILE, {key: password},
                                 example_path=os.path.join(settings_module.CONFIG_DIR, "realguard.env.example"))
    if password:
        os.environ[key] = password
    else:
        os.environ.pop(key, None)


def forget_password(camera_id: int) -> None:
    if os.getenv(password_key(camera_id)) is not None or _has_line(password_key(camera_id)):
        save_password(camera_id, "")


def _has_line(key: str) -> bool:
    try:
        with open(settings_module.ENV_FILE, encoding="utf-8-sig") as f:
            return any(line.strip().startswith(key + "=") for line in f)
    except OSError:
        return False


def is_env_camera(cam: Dict[str, Any]) -> bool:
    """Câmera padrão: a do CAMERA_ID do realguard.env (é a que o sistema abre ao ligar) ou cadastro sem IP."""
    try:
        default_id = int(os.getenv("CAMERA_ID", "1") or 1)
    except ValueError:
        default_id = 1
    if cam.get("id") is not None and int(cam["id"]) == default_id and not os.getenv(password_key(default_id)):
        return True
    return cam.get("source_kind") != "webcam" and not (cam.get("host") or "").strip()


def source_for(cam: Dict[str, Any], password: Optional[str] = None) -> str:
    if cam.get("source_kind") == "webcam":
        return str(int(cam.get("webcam_index") or 0))
    if is_env_camera(cam):
        return settings_module.resolve_camera_source()
    return setup_wizard.build_rtsp_url(cam.get("username") or "", password or stored_password(cam),
                                       cam["host"], str(cam.get("port") or 554),
                                       cam.get("path") or setup_wizard.DEFAULT_RTSP_PATH)


def can_stream(cam: Dict[str, Any]) -> bool:
    """Dá para abrir esta câmera sem pedir nada? (webcam, câmera do .env ou senha guardada)."""
    if cam.get("ficticio"):
        return False
    if cam.get("source_kind") == "webcam" or is_env_camera(cam):
        return True
    return not (cam.get("username") or "") or has_stored_password(cam)
