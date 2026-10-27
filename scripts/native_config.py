"""Ponte local da janela Windows para a validação compartilhada do RealGuard.

Recebe pares chave/base64 pela entrada padrão; senhas nunca entram na linha de comando.
"""

import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def active_server():
    path = os.path.join(ROOT, "logs", "active_server.json")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        url = data["url"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/auth/state", timeout=1) as response:
            return data if response.status == 200 else None
    except (OSError, ValueError, KeyError):
        return None


def needs_admin():
    from config.settings import Settings
    from database.migrations import initialize_database
    from database import access
    settings = Settings()
    initialize_database(settings.camera_id, settings.camera_name)
    return access.count_users(active_admins_only=True) == 0


def save(payload):
    from config.preconfig import save as save_config
    required = needs_admin()
    username = str(payload.get("admin_user") or "").strip().lower()
    password = str(payload.get("admin_password") or "")
    if required:
        from database.access import USERNAME_RE
        from app.security.passwords import password_problem
        if not USERNAME_RE.fullmatch(username):
            raise ValueError("O usuário administrador deve ter entre 3 e 32 caracteres válidos.")
        problem = password_problem(password, username)
        if problem:
            raise ValueError(problem)
    save_config(payload)
    if required:
        from database import access
        access.create_user(username, str(payload.get("admin_name") or username).strip(),
                           "admin", password, must_change_password=False)
    running = active_server()
    if running:
        from app.lifecycle import control_file
        with open(control_file(), "w", encoding="utf-8") as stream:
            stream.write("restart\n")
    return {"ok": True, "restarting": bool(running), "old_pid": running.get("pid") if running else None}


def main():
    from config.preconfig import read, test_connection
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "get":
        server = active_server()
        result = dict(read(), needs_admin=needs_admin(), server_pid=server.get("pid") if server else "", ok=True)
    elif action in ("save", "test"):
        payload = {}
        for line in sys.stdin:
            key, encoded = line.rstrip("\r\n").split("\t", 1)
            payload[key] = base64.b64decode(encoded).decode("utf-8")
        payload["proxy_enabled"] = payload.get("proxy_enabled") == "true"
        result = save(payload) if action == "save" else test_connection(payload, payload["target"])
    else:
        raise ValueError("Comando inválido.")
    emit(result)


def emit(result):
    for key, value in result.items():
        text = "true" if value is True else "false" if value is False else "" if value is None else str(value)
        print(key + "\t" + base64.b64encode(text.encode("utf-8")).decode("ascii"), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit({"ok": False, "message": str(exc)})
        sys.exit(1)
