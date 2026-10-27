"""Supervisiona o RealGuard no Windows sem exibir terminal."""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "console.log")
ADDRESS_PATH = os.path.join(LOG_DIR, "active_server.json")
TOKEN = secrets.token_urlsafe(32)
state = {"process": None, "started": 0.0, "last_request": time.monotonic()}


def _active_address():
    try:
        with open(ADDRESS_PATH, "r", encoding="utf-8") as stream:
            address = json.load(stream)
        url = address["url"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/auth/state", timeout=1.5) as response:
            if response.status == 200:
                return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def _needs_admin():
    from config.settings import Settings
    from database.migrations import initialize_database
    from database import access
    settings = Settings()
    initialize_database(settings.camera_id, settings.camera_name)
    return access.count_users(active_admins_only=True) == 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, status, content, content_type="application/json", cookie=""):
        body = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        from urllib.parse import parse_qs, urlsplit
        return parse_qs(urlsplit(self.path).query).get("token", [""])[0] == TOKEN

    def do_GET(self):
        from urllib.parse import urlsplit
        state["last_request"] = time.monotonic()
        path = urlsplit(self.path).path
        if path == "/ui/i18n.js":
            with open(os.path.join(BASE, "web", "i18n.js"), "rb") as stream:
                return self._send(200, stream.read(), "text/javascript")
        if path == "/api/ui/locale":
            from config.preconfig import read
            return self._send(200, {"locale": read()["locale"]})
        if not self._authorized():
            return self._send(403, {"detail": "Acesso negado."})
        if path == "/setup":
            from config.preconfig import read
            with open(os.path.join(BASE, "web", "preconfig.html"), "rb") as stream:
                return self._send(200, stream.read(), "text/html",
                                  "rg_ui_default=" + read()["locale"] + "; SameSite=Lax; Path=/")
        if path == "/setup/config":
            from config.preconfig import read
            return self._send(200, dict(read(), needs_admin=_needs_admin()))
        if path == "/setup/ready":
            process = state["process"]
            address = _active_address() if process else None
            return self._send(200, {"ready": bool(address), "url": address or "",
                                    "failed": bool(process and process.poll() is not None and not address)})
        return self._send(404, {"detail": "Página não encontrada."})

    def do_POST(self):
        from urllib.parse import urlsplit
        state["last_request"] = time.monotonic()
        if not self._authorized():
            return self._send(403, {"detail": "Acesso negado."})
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 32768 or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return self._send(400, {"detail": "Dados inválidos."})
        try:
            payload = json.loads(self.rfile.read(size))
            path = urlsplit(self.path).path
            if path.startswith("/setup/test/"):
                from config.preconfig import test_connection
                return self._send(200, test_connection(payload, path.rsplit("/", 1)[-1]))
            if path != "/setup/config":
                return self._send(404, {"detail": "Página não encontrada."})
            if state["process"] and state["process"].poll() is None:
                return self._send(409, {"detail": "O RealGuard já está iniciando."})
            from config.preconfig import save
            from app.security.passwords import password_problem
            needs_admin = _needs_admin()
            username = str(payload.get("admin_user", "")).strip().lower()
            password = str(payload.get("admin_password", ""))
            if needs_admin:
                from database.access import USERNAME_RE
                if not USERNAME_RE.match(username):
                    raise ValueError("Usuário administrador inválido (3 a 32 caracteres).")
                problem = password_problem(password, username)
                if problem:
                    raise ValueError(problem)
            save(payload)
            if needs_admin:
                from database import access
                access.create_user(username, str(payload.get("admin_name") or username).strip(),
                                   "admin", password, must_change_password=False)
            if os.path.isfile(ADDRESS_PATH):
                os.remove(ADDRESS_PATH)
            log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
            command = [sys.executable, os.path.join(BASE, "main.py"), "--no-setup", "--no-browser"]
            from app.lifecycle import _saved_env
            from config.settings import ENV_FILE
            state["process"] = subprocess.Popen(command, cwd=BASE, stdin=subprocess.DEVNULL,
                                                   stdout=log, stderr=subprocess.STDOUT,
                                                   env={**os.environ, **_saved_env(ENV_FILE)})
            state["started"] = time.monotonic()
            log.close()
            return self._send(200, {"status": "starting"})
        except ValueError as exc:
            return self._send(400, {"detail": str(exc)})
        except Exception as exc:
            with open(LOG_PATH, "a", encoding="utf-8") as log:
                traceback.print_exc(file=log)
            return self._send(500, {"detail": "Falha ao iniciar. Consulte logs/console.log.", "error": type(exc).__name__})


def main():
    if os.name != "nt":
        return
    existing = _active_address()
    if existing:
        webbrowser.open(existing)
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webbrowser.open(f"http://127.0.0.1:{server.server_port}/setup?token={TOKEN}")
    try:
        while True:
            process = state["process"]
            if process and process.poll() is not None:
                break
            if not process and time.monotonic() - state["last_request"] > 1800:
                break
            time.sleep(1)
    finally:
        server.shutdown()
        server.server_close()


def direct_main():
    """Usado após salvar na janela nativa do executável."""
    existing = _active_address()
    if existing:
        webbrowser.open(existing)
        return
    from app.lifecycle import _saved_env
    from config.settings import ENV_FILE
    try:
        os.remove(ADDRESS_PATH)
    except FileNotFoundError:
        pass
    with open(LOG_PATH, "a", encoding="utf-8", buffering=1) as log:
        process = subprocess.Popen([sys.executable, os.path.join(BASE, "main.py"), "--no-setup", "--no-browser"],
                                   cwd=BASE, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, **_saved_env(ENV_FILE)})
    deadline = time.monotonic() + 120
    opened = False
    while time.monotonic() < deadline:
        address = _active_address()
        if address:
            webbrowser.open(address)
            opened = True
            break
        if process.poll() is not None:
            break
        time.sleep(0.5)
    if not opened:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, "O RealGuard não iniciou. Consulte logs/console.log.", "RealGuard", 0x10)
    process.wait()


if __name__ == "__main__":
    try:
        direct_main() if "--direct" in sys.argv else main()
    except Exception:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
