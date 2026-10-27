"""Controla parada/reinício do servidor sem perder o processo iniciador."""

import os
import signal
import subprocess
import sys
import threading
import time

RESTART_EXIT_CODE = 75
_server = None
_action = None
_lock = threading.Lock()
_watcher_started = False


def register_server(server):
    global _server, _action, _watcher_started
    with _lock:
        _server = server
        _action = None
        if not _watcher_started and os.name == "nt":
            _watcher_started = True
            threading.Thread(target=_watch_native_control, daemon=True, name="realguard-native-control").start()


def control_file():
    from config.settings import BASE_DIR
    return os.path.join(BASE_DIR, "logs", "native-control.request")


def _watch_native_control():
    path = control_file()
    try:
        os.remove(path)  # descarta pedidos antigos de uma execução anterior
    except FileNotFoundError:
        pass
    while True:
        try:
            with open(path, "r", encoding="utf-8") as stream:
                command = stream.read().strip()
            os.remove(path)
            if command == "restart":
                request("restart")
                return
        except FileNotFoundError:
            pass
        except OSError:
            pass
        time.sleep(0.5)


def action():
    with _lock:
        return _action


def request(action_name):
    """Chamado somente por rotas autenticadas, depois de responder ao navegador."""
    if action_name not in ("restart", "stop"):
        raise ValueError("Ação inválida")
    global _action
    with _lock:
        _action = action_name
        server = _server
    if server is not None:
        from app.engine import engine
        from app.camera.previews import previews
        engine.request_stop()
        previews.stop_all()
        server.should_exit = True


def _saved_env(path):
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8-sig") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def supervise(initial_mode, no_browser=False, host=None, port=None):
    """O terminal da IA BOX permanece; cada reinício recebe o .env atualizado."""
    from config.settings import BASE_DIR, ENV_FILE

    mode = initial_mode
    first = True
    while True:
        saved = _saved_env(ENV_FILE)
        if not first:
            mode = saved.get("REALGUARD_START_MODE", mode)
        env = os.environ.copy()
        env.update(saved)
        env["REALGUARD_START_MODE"] = mode
        command = [sys.executable, os.path.join(BASE_DIR, "main.py"), "--worker", "--no-setup",
                   "--arena" if mode == "arena" else "--complete"]
        if no_browser:
            command.append("--no-browser")
        if host:
            command.extend(("--host", host))
        if port:
            command.extend(("--port", str(port)))
        child = subprocess.Popen(command, cwd=BASE_DIR, env=env)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            if child.poll() is None:
                try:
                    if os.name == "nt":
                        child.terminate()
                    else:
                        child.send_signal(signal.SIGINT)
                    child.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    child.kill()
            return 0
        if code != RESTART_EXIT_CODE:
            return code
        first = False
