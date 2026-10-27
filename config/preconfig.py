"""Valores e validação da pré-configuração, compartilhados pela inicialização e API."""

import ipaddress
import os
import re
from urllib.parse import unquote, urlsplit

from app.lifecycle import _saved_env
from config import setup_wizard
from config.settings import ENV_FILE

LOCALES = ("pt-BR", "en", "es", "fr", "de", "ar", "hi", "zh-CN", "ja", "ru")
MODES = ("complete", "arena")
CAMERA_MODES = ("webcam", "rtsp", "auto")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")


def _clean(value, name, limit=255):
    value = str(value or "").strip()
    if len(value) > limit or any(char in value for char in "\r\n\x00"):
        raise ValueError(f"{name}: valor inválido.")
    return value


def _host(value, name):
    value = _clean(value, name)
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not HOST_RE.fullmatch(value):
            raise ValueError(f"{name}: informe um IP ou nome válido.")
    return value


def _port(value, name):
    value = _clean(value, name, 5)
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError(f"{name}: informe uma porta entre 1 e 65535.")
    return value


def read():
    env = {**os.environ, **_saved_env(ENV_FILE)}
    proxy = setup_wizard.proxy_parts(env.get("HTTPS_PROXY", ""))
    values = {
        "mode": env.get("REALGUARD_START_MODE", "complete"),
        "locale": env.get("REALGUARD_UI_LOCALE", "pt-BR"),
        "camera_mode": env.get("CAMERA_MODE", "webcam"),
        "camera_index": env.get("CAMERA_INDEX", "0"),
        "camera_ip": env.get("CAMERA_IP", setup_wizard.DEFAULT_CAMERA_IP),
        "camera_port": env.get("CAMERA_PORT", "554"),
        "camera_path": env.get("CAMERA_RTSP_PATH", setup_wizard.DEFAULT_RTSP_PATH),
        "camera_user": env.get("CAMERA_USER", ""),
        "camera_has_password": bool(env.get("CAMERA_PASS")),
        "proxy_enabled": bool(proxy),
        "proxy_host": proxy.get("ip", setup_wizard.DEFAULT_PROXY_IP),
        "proxy_port": proxy.get("port", setup_wizard.DEFAULT_PROXY_PORT),
        "proxy_user": proxy.get("user", ""),
        "proxy_has_password": bool(proxy.get("password")),
    }
    explicit = env.get("RTSP_URL", "").strip()
    if explicit.isdigit():
        values.update(camera_mode="webcam", camera_index=explicit)
    elif explicit.lower().startswith("rtsp://"):
        try:
            source = urlsplit(explicit)
            values.update(camera_mode="rtsp", camera_ip=source.hostname or values["camera_ip"],
                          camera_port=str(source.port or 554), camera_path=(source.path or "/") + ("?" + source.query if source.query else ""),
                          camera_user=unquote(source.username or ""), camera_has_password=bool(source.password))
        except ValueError:
            values["camera_mode"] = "rtsp"
    return values


def save(data):
    """Senhas em branco preservam as atuais; nunca são devolvidas ao navegador."""
    old = {**os.environ, **_saved_env(ENV_FILE)}
    mode = _clean(data.get("mode"), "Modo", 20)
    locale = _clean(data.get("locale"), "Idioma", 20)
    camera_mode = _clean(data.get("camera_mode"), "Fonte de vídeo", 20)
    if mode not in MODES or locale not in LOCALES or camera_mode not in CAMERA_MODES:
        raise ValueError("Modo, idioma ou fonte de vídeo inválido.")
    index = _clean(data.get("camera_index", "0"), "Índice da webcam", 2)
    if not index.isdigit() or int(index) > 15:
        raise ValueError("Índice da webcam: use um número entre 0 e 15.")
    updates = {"REALGUARD_START_MODE": mode, "REALGUARD_UI_LOCALE": locale,
               "CAMERA_MODE": camera_mode, "CAMERA_INDEX": index, "API_HOST": "auto"}
    if camera_mode in ("rtsp", "auto"):
        ip = _host(data.get("camera_ip"), "IP da câmera")
        port = _port(data.get("camera_port"), "Porta RTSP")
        path = _clean(data.get("camera_path"), "Caminho RTSP", 500)
        if not path.startswith("/"):
            raise ValueError("O caminho RTSP deve começar com /.")
        user = _clean(data.get("camera_user"), "Usuário da câmera", 100)
        password = _clean(data.get("camera_password"), "Senha da câmera", 200)
        explicit = old.get("RTSP_URL", "").strip()
        if explicit.lower().startswith("rtsp://"):
            password = password or unquote(urlsplit(explicit).password or "")
        updates.update({"CAMERA_IP": ip, "CAMERA_PORT": port, "CAMERA_RTSP_PATH": path,
                        "CAMERA_USER": user, "CAMERA_PASS": password or old.get("CAMERA_PASS", "")})
    updates["RTSP_URL"] = ""
    proxy_enabled = data.get("proxy_enabled") is True
    if proxy_enabled:
        host = _host(data.get("proxy_host"), "IP do proxy")
        port = _port(data.get("proxy_port"), "Porta do proxy")
        user = _clean(data.get("proxy_user"), "Usuário do proxy", 100)
        password = _clean(data.get("proxy_password"), "Senha do proxy", 200)
        old_proxy = setup_wizard.proxy_parts(old.get("HTTPS_PROXY", ""))
        password = password or old_proxy.get("password", "")
        updates["HTTPS_PROXY"] = setup_wizard.build_proxy_url(host, port, user, password)
        updates["NO_PROXY"] = f"127.0.0.1,localhost,{updates.get('CAMERA_IP', old.get('CAMERA_IP', ''))}"
    else:
        updates["HTTPS_PROXY"] = ""
        updates["NO_PROXY"] = ""
    setup_wizard.update_env_file(ENV_FILE, updates)
    return updates


def test_connection(data, target):
    old = {**os.environ, **_saved_env(ENV_FILE)}
    if target == "camera":
        ip = _host(data.get("camera_ip"), "IP da câmera")
        port = _port(data.get("camera_port"), "Porta RTSP")
        path = _clean(data.get("camera_path"), "Caminho RTSP", 500)
        if not path.startswith("/"):
            raise ValueError("O caminho RTSP deve começar com /.")
        user = _clean(data.get("camera_user"), "Usuário da câmera", 100)
        password = _clean(data.get("camera_password"), "Senha da câmera", 200) or old.get("CAMERA_PASS", "")
        explicit = old.get("RTSP_URL", "").strip()
        if not data.get("camera_password") and explicit.lower().startswith("rtsp://"):
            password = unquote(urlsplit(explicit).password or "") or password
        ok = setup_wizard.test_rtsp(setup_wizard.build_rtsp_url(user, password, ip, port, path))
        return {"ok": ok, "message": "Imagem recebida da câmera." if ok else "Não foi possível ler a câmera."}
    if target == "proxy":
        host = _host(data.get("proxy_host"), "IP do proxy")
        port = _port(data.get("proxy_port"), "Porta do proxy")
        user = _clean(data.get("proxy_user"), "Usuário do proxy", 100)
        previous = setup_wizard.proxy_parts(old.get("HTTPS_PROXY", ""))
        password = _clean(data.get("proxy_password"), "Senha do proxy", 200) or previous.get("password", "")
        problem = setup_wizard.test_proxy(setup_wizard.build_proxy_url(host, port, user, password))
        return {"ok": not problem, "message": "Internet disponível pelo proxy." if not problem else problem}
    raise ValueError("Teste inválido.")
