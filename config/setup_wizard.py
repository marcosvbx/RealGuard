"""
Assistente de configuração no terminal (roda ao iniciar `python main.py`).

Pergunta se a fonte de vídeo é a webcam ou a câmera IP (RTSP) e, no caso do
RTSP, pede as credenciais ANTES de o sistema subir. Tudo é opcional: apertar
Enter mantém o que já está no config/realguard.env.

Segurança:
  * a senha é digitada com getpass (não aparece na tela nem no histórico);
  * nada é salvo sem perguntar; se salvar, o arquivo fica com permissão 600
    (Linux/AIBOX) e continua fora do Git (.gitignore);
  * o assistente NÃO roda quando não há terminal interativo (ex.: serviço de
    boot/systemd) e pode ser desligado de vez com INTERACTIVE_SETUP=false no
    realguard.env ou com `python main.py --no-setup` — o caminho indicado
    para uma implantação real, em que o arquivo é editado à mão.
"""

import os
import shutil
import sys
import threading
from typing import Dict, Optional
from urllib.parse import quote, unquote, urlsplit

from config import console as tui

DEFAULT_CAMERA_IP = "192.168.50.108"   # câmera Intelbras do laboratório (manual do professor)
DEFAULT_RTSP_PATH = "/cam/realmonitor?channel=1&subtype=1"
RTSP_TEST_TIMEOUT_SEC = 10.0
DEFAULT_PROXY_IP = "192.168.50.231"    # notebook mestre no cabo da AIBOX (roteiro da feira)
DEFAULT_PROXY_PORT = "8899"
PROXY_TEST_URL = "https://api.telegram.org"
PROXY_TEST_TIMEOUT_SEC = 10.0


class SetupAborted(Exception):
    """Usuário cancelou (Ctrl+C) durante as perguntas."""


# ---------------------------------------------------------------- utilidades
def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on", "sim", "s")


def should_run(force: bool = False, disabled: bool = False) -> bool:
    """Roda só com terminal interativo e se não tiver sido desligado."""
    if disabled:
        return False
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    if force:
        return True
    return _truthy(os.getenv("INTERACTIVE_SETUP", "true"))


def _ask(prompt: str, default: str = "") -> str:
    try:
        return tui.ask(prompt, default)
    except tui.Cancelled:
        raise SetupAborted()


def _yes(prompt: str, default: bool) -> bool:
    try:
        return tui.ask_yes(prompt, default)
    except tui.Cancelled:
        raise SetupAborted()


def _secret(prompt: str, has_current: bool) -> Optional[str]:
    """Lê a senha sem eco. None = manter a atual."""
    try:
        value = tui.ask_secret(prompt, keep_hint=has_current)
    except tui.Cancelled:
        raise SetupAborted()
    if not value and has_current:
        return None
    # Digite a senha normal, SEM aspas. Se alguém digitar 'senha' com as aspas
    # do .env por costume, elas são removidas.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def mask_source(env: Dict[str, str]) -> str:
    """Descrição da fonte atual sem expor usuário/senha."""
    explicit = (env.get("RTSP_URL") or "").strip()
    if explicit and not explicit.isdigit():
        host = explicit.rsplit("@", 1)[-1] if "@" in explicit else explicit.split("://", 1)[-1]
        return f"Câmera IP (RTSP_URL) rtsp://***@{host.split('/')[0]}"
    if (env.get("CAMERA_MODE") or "").strip().lower() == "auto":
        return f"Automática: câmera IP {env.get('CAMERA_IP') or '?'} se responder, senão a webcam"
    if (env.get("CAMERA_MODE") or "webcam").strip().lower() == "rtsp":
        ip = env.get("CAMERA_IP") or "?"
        port = env.get("CAMERA_PORT") or "554"
        user = "definido" if env.get("CAMERA_USER") else "sem usuário"
        pwd = "senha salva" if env.get("CAMERA_PASS") else "sem senha"
        path = env.get("CAMERA_RTSP_PATH") or DEFAULT_RTSP_PATH
        return f"Câmera IP (RTSP) {ip}:{port}{path} · {user} · {pwd}"
    return f"Webcam do computador (índice {env.get('CAMERA_INDEX') or '0'})"


def build_rtsp_url(user: str, password: str, ip: str, port: str, path: str = DEFAULT_RTSP_PATH) -> str:
    return f"rtsp://{user}:{quote(password or '', safe='')}@{ip}:{port}{path}"


def test_rtsp(url: str, timeout: float = RTSP_TEST_TIMEOUT_SEC) -> bool:
    """Tenta abrir a câmera e ler 1 frame, com limite de tempo (o OpenCV pode travar ~30 s)."""
    result = {"ok": False}

    def _probe():
        try:
            import cv2
            cap = cv2.VideoCapture(url)
            ok = cap.isOpened() and cap.read()[0]
            cap.release()
            result["ok"] = bool(ok)
        except Exception:
            result["ok"] = False

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout)
    return result["ok"]


def update_env_file(path: str, updates: Dict[str, str], example_path: Optional[str] = None) -> None:
    """
    Grava os valores no realguard.env preservando comentários e a ordem.
    Chave existente (não comentada) é substituída; chave nova vai no final.
    """
    if not os.path.isfile(path) and example_path and os.path.isfile(example_path):
        shutil.copyfile(example_path, path)
    lines = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()

    pending = dict(updates)
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in pending:
            lines[i] = f"{key}={_format_value(key, pending.pop(key))}"
    if pending:
        lines.append("")
        lines.append("# Gravado pelo assistente de configuração (python main.py)")
        lines.extend(f"{k}={_format_value(k, v)}" for k, v in pending.items())

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)   # só o dono lê (senha/token). No Windows é ignorado.
    except OSError:
        pass


def _format_value(key: str, value: str) -> str:
    """Senha sempre entre aspas simples (use a senha configurada na sua câmera)."""
    value = "" if value is None else str(value)
    if (key == "CAMERA_PASS" or key.startswith("CAMERA_PASS_")) and value and "'" not in value:
        return f"'{value}'"
    return _quote_value(value)


def _quote_value(value: str) -> str:
    """Aspas simples quando há caracteres que o leitor do .env trataria mal."""
    # O leitor (settings._load_env_file) só remove UM par de aspas iguais nas
    # pontas e não interpreta mais nada — então nunca alteramos o conteúdo,
    # só escolhemos um tipo de aspas que não apareça no valor.
    value = "" if value is None else str(value)
    needs = value != value.strip() or any(c in value for c in " #$\t") or (
        len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"")
    if not needs:
        return value
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return value


# ---------------------------------------------------------------- fluxo
def run(env_path: str, example_path: Optional[str] = None) -> Dict[str, str]:
    """
    Pergunta só o necessário (webcam ou câmera IP; no RTSP, as credenciais) e
    aplica as respostas em os.environ (valem para esta execução). Retorna o que
    mudou. Levanta SetupAborted se o usuário cancelar.

    O endereço do dashboard NÃO é perguntado: fica automático (API_HOST=auto),
    127.0.0.1 com webcam e 0.0.0.0 com câmera IP — ver settings.resolve_api_host.
    """
    env = os.environ
    tui.header("Câmera", "Configuração rápida " + tui.G["dot"] + " para pular: python main.py --no-setup")
    tui.say(f"Fonte atual: {tui.c(mask_source(env), 'white', 'bold')}", indent=4)
    tui.out()
    tui.options([("Enter", "continuar com a atual"), ("1", "Webcam do computador"), ("2", "Câmera IP (RTSP)")])
    choice = _ask("Escolha", "").strip()
    while choice not in ("", "1", "2"):
        tui.say("Digite 1, 2 ou apenas Enter.", "bad", indent=6)
        choice = _ask("Escolha", "").strip()

    updates: Dict[str, str] = {}
    if choice == "":
        tui.say("Mantendo a câmera atual.", "ok")
    else:
        if choice == "1":
            # Webcam padrão do computador, sem perguntar índice. Quem tiver mais de
            # uma câmera ajusta CAMERA_INDEX no realguard.env.
            updates = {"CAMERA_MODE": "webcam", "CAMERA_INDEX": env.get("CAMERA_INDEX") or "0"}
            if (env.get("RTSP_URL") or "").strip():
                updates["RTSP_URL"] = ""   # URL antiga teria prioridade sobre o modo
        else:
            updates = _ask_rtsp(env)
        updates["API_HOST"] = "auto"       # endereço do dashboard acompanha a fonte
        for key, value in updates.items():
            os.environ[key] = value

    tui.out()
    proxy = _ask_proxy(env)
    for key, value in proxy.items():
        os.environ[key] = value
    updates.update(proxy)

    if not updates:
        tui.out()
        return {}

    tui.out()
    if _yes("Salvar em config/realguard.env para as próximas vezes?", True):
        update_env_file(env_path, updates, example_path)
        tui.say(f"Salvo em {os.path.relpath(env_path)}\nsó o seu usuário consegue ler o arquivo", "ok")
    else:
        tui.say("Vale só para esta execução (nada foi gravado).", "info")
    tui.out()
    return updates


def _ask_rtsp(env) -> Dict[str, str]:
    while True:
        tui.subheader("Câmera IP", "Enter aceita o valor entre parênteses")
        ip = _ask("IP da câmera", env.get("CAMERA_IP") or DEFAULT_CAMERA_IP)
        port = _ask("Porta RTSP", env.get("CAMERA_PORT") or "554")
        path = _ask_rtsp_path(env.get("CAMERA_RTSP_PATH") or DEFAULT_RTSP_PATH)
        user = _ask("Usuário", env.get("CAMERA_USER") or "")
        password = _secret("Senha (sem aspas)", bool(env.get("CAMERA_PASS")))
        if password is None:
            password = env.get("CAMERA_PASS", "")

        values = {"CAMERA_MODE": "rtsp", "CAMERA_IP": ip, "CAMERA_PORT": port, "CAMERA_RTSP_PATH": path,
                  "CAMERA_USER": user, "CAMERA_PASS": password}
        if (env.get("RTSP_URL") or "").strip():
            values["RTSP_URL"] = ""    # a URL antiga teria prioridade sobre estes campos

        tui.out()
        if not _yes("Testar a conexão com a câmera agora?", True):
            return values
        tui.say(f"Conectando em {ip}:{port} (até {int(RTSP_TEST_TIMEOUT_SEC)} s){tui.G['dots']}", "info", indent=4)
        if test_rtsp(build_rtsp_url(user, password, ip, port, path)):
            tui.say("Câmera OK: imagem recebida.", "ok", indent=4)
            return values
        tui.say("Não consegui ler a câmera.\n"
                "Confira o IP (rede 192.168.50.x), o cabo, o caminho do modelo, o usuário (maiúsculas contam) e a senha.",
                "bad", indent=4)
        tui.out()
        tui.options([("1", "digitar de novo"), ("2", "iniciar assim mesmo (o sistema tenta reconectar sozinho)")])
        if _ask("Escolha", "1").strip() != "2":
            env = dict(env, **values)   # próxima rodada sugere o que acabou de ser digitado
            continue
        return values


RTSP_PATHS = [("1", "Padrão (Intelbras / Dahua)", DEFAULT_RTSP_PATH),
              ("2", "Haiz", "/avstream/channel=1/stream=0.sdp")]
MANUAL_PATH_OPTION = "3"


def path_from_answer(answer: str, current: str) -> str:
    """Caminho RTSP a partir do que foi digitado: opção, caminho (/...) ou a URL rtsp:// inteira colada."""
    answer = (answer or "").strip()
    if not answer:
        return current
    for key, _, path in RTSP_PATHS:
        if answer == key:
            return path
    if answer.lower().startswith("rtsp://"):
        rest = answer.split("://", 1)[1]
        rest = rest.rsplit("@", 1)[-1] if "@" in rest.split("/", 1)[0] else rest
        return "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return answer if answer.startswith("/") else "/" + answer


def _ask_rtsp_path(current: str) -> str:
    """Cada modelo de câmera tem um caminho de vídeo diferente (o que vem depois de IP:porta)."""
    tui.say(f"Caminho do vídeo (muda conforme o modelo da câmera). Atual: {tui.c(current, 'white', 'bold')}", indent=4)
    tui.options([("Enter", "manter o atual")] + [(k, f"{name}  {path}") for k, name, path in RTSP_PATHS]
                + [(MANUAL_PATH_OPTION, "digitar outro caminho (ou colar a URL rtsp:// inteira)")])
    answer = _ask("Escolha", "").strip()
    if answer == MANUAL_PATH_OPTION:
        answer = ""
        while not answer:
            answer = _ask("Caminho ou URL rtsp://", "").strip()
    return path_from_answer(answer, current)


# ---------------------------------------------------------------- internet (proxy)
def proxy_parts(url: str) -> Dict[str, str]:
    """Separa http://usuario:senha@ip:porta em partes (vazio se não houver proxy)."""
    url = (url or "").strip()
    if not url:
        return {}
    try:
        u = urlsplit(url if "://" in url else "http://" + url)
        return {"ip": u.hostname or "", "port": str(u.port or ""), "user": unquote(u.username or ""),
                "password": unquote(u.password or "")}
    except ValueError:
        return {}


def build_proxy_url(ip: str, port: str, user: str, password: str) -> str:
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    return f"http://{auth}{ip}:{port}"


def describe_proxy(url: str) -> str:
    p = proxy_parts(url)
    if not p:
        return "Direta (sem proxy)"
    who = "com usuário e senha" if p.get("user") else "sem senha"
    return f"Pelo proxy {p['ip']}:{p['port']} {tui.G['dot']} {who}"


def test_proxy(url: str, timeout: float = PROXY_TEST_TIMEOUT_SEC) -> str:
    """'' = internet OK pelo proxy; senão, o motivo em português."""
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}))
    try:
        opener.open(PROXY_TEST_URL, timeout=timeout).close()
        return ""
    except urllib.error.HTTPError as e:
        if e.code == 407:
            return "o proxy recusou o usuário ou a senha (erro 407)"
        return ""   # chegou ao Telegram (qualquer resposta HTTP prova que há internet)
    except Exception as e:
        text = str(getattr(e, "reason", e))
        if "407" in text:
            return "o proxy recusou o usuário ou a senha (erro 407)"
        if "Tunnel connection failed" in text:
            return f"o proxy respondeu, mas não liberou a internet ({text.split(':', 1)[-1].strip()})"
        if "refused" in text.lower() or "recusad" in text.lower():
            return "o proxy não está aberto nesse IP e porta (conexão recusada)"
        if "timed out" in text.lower() or "timeout" in text.lower():
            return "o proxy não respondeu (desligado, firewall ou IP errado)"
        return f"sem resposta ({text})"


def _ask_proxy(env) -> Dict[str, str]:
    """Pergunta como o sistema chega à internet (Telegram e assistente). Vazio = manter."""
    current = (env.get("HTTPS_PROXY") or env.get("https_proxy") or "").strip()
    tui.header("Internet", "Telegram e assistente " + tui.G["dot"] + " proxy só na AIBOX da feira")
    tui.say(f"Atual: {tui.c(describe_proxy(current), 'white', 'bold')}", indent=4)
    tui.out()
    tui.options([("Enter", "continuar com a atual"), ("1", "Direta (computador com internet)"),
                 ("2", "Pelo proxy do notebook mestre (AIBOX)")])
    choice = _ask("Escolha", "").strip()
    while choice not in ("", "1", "2"):
        tui.say("Digite 1, 2 ou apenas Enter.", "bad", indent=6)
        choice = _ask("Escolha", "").strip()
    if choice == "":
        tui.say("Mantendo a internet atual.", "ok")
        return {}
    if choice == "1":
        if (env.get("https_proxy") or "").strip():
            os.environ.pop("https_proxy", None)
        tui.say("Internet direta, sem proxy.", "ok")
        return {"HTTPS_PROXY": "", "NO_PROXY": ""}

    old = proxy_parts(current)
    while True:
        tui.subheader("Proxy", "Enter aceita o valor entre parênteses")
        ip = _ask("IP do notebook mestre", old.get("ip") or DEFAULT_PROXY_IP)
        port = _ask("Porta do proxy", old.get("port") or DEFAULT_PROXY_PORT)
        user = _ask("Usuário do proxy", old.get("user") or "")
        password = _secret("Senha do proxy", bool(old.get("password")))
        if password is None:
            password = old.get("password", "")
        url = build_proxy_url(ip, port, user, password)
        camera = env.get("CAMERA_IP") or DEFAULT_CAMERA_IP
        values = {"HTTPS_PROXY": url, "NO_PROXY": f"127.0.0.1,localhost,{camera}"}

        tui.out()
        if not _yes("Testar a internet pelo proxy agora?", True):
            return values
        tui.say(f"Testando {ip}:{port} (até {int(PROXY_TEST_TIMEOUT_SEC)} s){tui.G['dots']}", "info", indent=4)
        problem = test_proxy(url)
        if not problem:
            tui.say("Internet OK pelo proxy: o Telegram responde.", "ok", indent=4)
            return values
        tui.say(f"Não deu: {problem}.\n"
                "Confira se o proxy está aberto no mestre, a regra do firewall (porta 8899),\n"
                "o cabo e se usuário e senha são os mesmos do comando do proxy.", "bad", indent=4)
        tui.out()
        tui.options([("1", "digitar de novo"), ("2", "seguir assim mesmo"), ("3", "seguir sem proxy")])
        again = _ask("Escolha", "1").strip()
        if again == "2":
            return values
        if again == "3":
            return {"HTTPS_PROXY": "", "NO_PROXY": ""}
        old = {"ip": ip, "port": port, "user": user, "password": password}
