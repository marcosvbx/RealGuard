#!/usr/bin/env python3
"""
RealGuard — ponto único de inicialização.

    python main.py                       # pergunta webcam/RTSP e sobe tudo
    python main.py --no-setup            # sobe direto com o config/realguard.env
    python main.py --setup               # força as perguntas (mesmo com INTERACTIVE_SETUP=false)
    python main.py --host 0.0.0.0        # acessível na rede (AIBOX)
    python main.py --port 9000

Comandos de administração (não sobem o sistema):

    python main.py --criar-admin             # cria o primeiro administrador (ou outro)
    python main.py --redefinir-senha USUARIO # senha nova para quem esqueceu
    python main.py --backup                  # backup agora (banco + fotos dos alertas)
    python main.py --listar-backups
    python main.py --restaurar NOME.zip      # com o sistema PARADO, de preferência

Dados fictícios (no banco real, marcados e removíveis):

    Na 1ª subida com o banco sem alunos, o sistema cria sozinho 120 alunos e
    90 dias fictícios (sem usuários, sem Telegram, sem rostos).
    python main.py --remover-ficticios       # apaga só o que é fictício (com backup antes)
    DADOS_FICTICIOS=off no realguard.env     # nunca criar

Um único processo, um único terminal: a captura, o reconhecimento, o registro
de presença e o Telegram rodam o tempo todo em segundo plano. O dashboard
(http://127.0.0.1:8000) é só uma janela para acompanhar e cadastrar.
"""

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Avisos internos do OpenCV/FFmpeg (ex.: "[ WARN:0@5.0] can't open camera by index") poluíam o
# terminal a cada nova tentativa da câmera. O RealGuard já avisa do jeito dele; aqui só erros.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

from config import settings as settings_module  # noqa: E402  (carrega config/realguard.env)
from config import setup_wizard  # noqa: E402


# ---------------------------------------------------------------- comandos de terminal
def _prepare_db():
    from database.migrations import initialize_database
    s = settings_module.Settings()
    initialize_database(s.camera_id, s.camera_name)


def _ask_new_password(username: str) -> str:
    from app.security.passwords import password_problem
    from config import console as tui
    while True:
        first = tui.ask_secret("Senha (8+ caracteres)")
        problem = password_problem(first, username)
        if problem:
            tui.say(problem, "bad", indent=6)
            continue
        if tui.ask_secret("Repita a senha") != first:
            tui.say("As senhas não conferem. Tente de novo.", "bad", indent=6)
            continue
        return first


def cmd_create_admin() -> int:
    from app.security.permissions import ROLE_ADMIN
    from config import console as tui
    from database import access
    _prepare_db()
    tui.header("Criar administrador",
               "Já existe administrador: este comando cria mais um." if access.count_users(active_admins_only=True)
               else "Primeiro acesso ao painel " + tui.G["dot"] + " Ctrl+C cancela")

    def check_username(value: str):
        value = value.strip().lower()
        if not access.USERNAME_RE.match(value):
            return "Use 3 a 32 caracteres: letras minúsculas, números, ponto, hífen ou _."
        if _user_has_password(value):
            return f"Esse usuário já existe. Para trocar a senha: python main.py --redefinir-senha {value}"
        return None
    try:
        username = tui.ask("Usuário (ex.: admin, joao.silva)", validate=check_username).strip().lower()
        full_name = tui.ask("Nome da pessoa", username).strip() or username
        password = _ask_new_password(username)
    except tui.Cancelled:
        tui.say("Cancelado.", "warn")
        return 1
    try:
        access.create_user(username, full_name, ROLE_ADMIN, password, must_change_password=False)
    except access.AccessError as e:
        tui.say(str(e), "bad")
        return 1
    access.log_audit("usuario_criado", target_type="usuario", detail=f"{username} (admin) pelo terminal",
                     username="terminal")
    tui.out()
    tui.say(f"Administrador {tui.c(username, 'bold')} criado.\nEntre no painel com esse usuário.", "ok")
    tui.out()
    return 0


def _user_has_password(username: str) -> bool:
    from database import access
    return any(u["username"] == username for u in access.list_users())


def cmd_reset_password(username: str) -> int:
    from config import console as tui
    from database import access
    _prepare_db()
    tui.header("Redefinir senha", f"Usuário: {username} " + tui.G["dot"] + " Ctrl+C cancela")
    user = access.get_user_by_username(username)
    if user is None or not _user_has_password(user["username"]):
        tui.say(f"Usuário {username} não encontrado.", "bad")
        return 1
    try:
        password = _ask_new_password(user["username"])
    except tui.Cancelled:
        tui.say("Cancelado.", "warn")
        return 1
    access.set_password(user["id"], password, must_change=False)
    access.delete_user_sessions(user["id"])
    access.log_audit("senha_redefinida", target_type="usuario", target_id=user["id"],
                     detail=f"{user['username']} pelo terminal", username="terminal")
    tui.out()
    tui.say(f"Senha de {tui.c(user['username'], 'bold')} redefinida.\nAs sessões abertas dele foram encerradas.", "ok")
    tui.out()
    return 0


def cmd_backup() -> int:
    from config import console as tui
    from database import backup
    _prepare_db()
    tui.header("Backup")
    try:
        info = backup.create_backup("manual")
    except backup.BackupError as e:
        tui.say(str(e), "bad")
        return 1
    tui.say(f"Backup criado ({info['tamanho'] // 1024} KB)\n{os.path.join(backup.backup_dir(), info['nome'])}", "ok")
    tui.out()
    return 0


def cmd_list_backups() -> int:
    from config import console as tui
    from database import backup
    items = backup.list_backups()
    tui.header("Backups", backup.backup_dir())
    if not items:
        tui.say("Nenhum backup ainda.", "info")
    for b in items:
        tui.say(f"{b['nome']:<44} {b['tamanho'] // 1024:>8} KB   {tui.c(b['tipo'], 'grey')}", indent=4)
    tui.out()
    return 0


def cmd_restore(name: str) -> int:
    from config import console as tui
    from database import backup
    tui.header("Restaurar backup", name)
    tui.say("O banco atual será substituído (antes é feito um backup dele).", "warn")
    tui.out()
    try:
        if not tui.ask_yes("Restaurar agora?", False):
            tui.say("Cancelado.", "info")
            return 1
    except tui.Cancelled:
        return 1
    try:
        result = backup.restore_backup(os.path.basename(name))
    except backup.BackupError as e:
        tui.say(str(e), "bad")
        return 1
    _prepare_db()   # backup de versão antiga: migra até o schema atual
    tui.say(f"Restaurado.\nBackup do estado anterior: {result['backup_de_seguranca'] or '-'}", "ok")
    tui.out()
    return 0


def cmd_remove_sample() -> int:
    from config import console as tui
    _prepare_db()
    from database import backup, dados_ficticios
    tui.header("Remover dados fictícios")
    state = dados_ficticios.get_state()
    if not state["registros"]["alunos"] and not state["registros"]["alertas"]:
        tui.say("Não há dados fictícios no banco.", "info")
        return 0
    try:
        b = backup.create_backup("manual")
        tui.say(f"Backup antes de remover: {b['nome']}", "ok")
    except Exception as e:
        tui.say(f"Não consegui fazer o backup ({e}).", "warn")
    n = dados_ficticios.remove()
    tui.say(f"Removidos {n['alunos']} alunos, {n['passagens']} passagens, {n['alertas']} alertas e "
            f"{n['cameras']} câmeras fictícias.\nO que é real ficou intacto.", "ok")
    tui.out()
    return 0


def _seed_sample_data() -> None:
    """1ª subida com o banco vazio: cria os dados fictícios (uma vez só)."""
    from config import console as tui
    from database import dados_ficticios
    if not dados_ficticios.should_seed():
        n = dados_ficticios.get_state()["registros"]["alunos"]
        if n:
            tui.step("Dados fictícios", f"{n} alunos de demonstração (Administração → Dados fictícios)", "info")
        return
    tui.step("Dados fictícios", "criando 120 alunos e 90 dias de demonstração" + tui.G["dots"], "wait")
    try:
        info = dados_ficticios.generate(quiet=True)
        tui.step("Dados fictícios", f"criados ({info['de'][8:10]}/{info['de'][5:7]} a "
                                    f"{info['ate'][8:10]}/{info['ate'][5:7]}; sem usuários, Telegram ou rostos)")
    except Exception as e:
        tui.step("Dados fictícios", f"não criados ({e}); o sistema sobe normalmente", "warn")


# ---------------------------------------------------------------- feira e câmera automática
def _truthy(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "sim", "s", "yes", "on")


def _webcam_available(index: int) -> bool:
    try:
        import cv2
        cap = cv2.VideoCapture(index)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        return bool(ok)
    except Exception:
        return False


def _resolve_camera_auto() -> None:
    """
    CAMERA_MODE=auto (o mesmo realguard.env serve na AIBOX e no notebook):
      * câmera IP configurada e respondendo  -> usa a câmera IP;
      * não respondeu e há webcam           -> usa a webcam (ex.: notebook fora da rede do laboratório);
      * não respondeu e não há webcam       -> fica na câmera IP e o sistema reconecta sozinho (ex.: AIBOX).
    """
    from config import console as tui
    if os.getenv("CAMERA_MODE", "webcam").strip().lower() != "auto" or os.getenv("RTSP_URL", "").strip():
        return
    ip = os.getenv("CAMERA_IP", "").strip()
    index = int(os.getenv("CAMERA_INDEX", "0") or 0) if os.getenv("CAMERA_INDEX", "0").strip().isdigit() else 0
    if ip:
        tui.step("Câmera", f"procurando a câmera IP {ip}" + tui.G["dots"], "wait")
        url = setup_wizard.build_rtsp_url(os.getenv("CAMERA_USER", ""), os.getenv("CAMERA_PASS", ""), ip,
                                          os.getenv("CAMERA_PORT", "554"),
                                          os.getenv("CAMERA_RTSP_PATH", setup_wizard.DEFAULT_RTSP_PATH))
        if setup_wizard.test_rtsp(url, timeout=float(os.getenv("CAMERA_AUTO_TIMEOUT", "8") or 8)):
            os.environ["CAMERA_MODE"] = "rtsp"
            tui.step("Câmera", f"câmera IP {ip} respondeu")
            return
        if _webcam_available(index):
            os.environ["CAMERA_MODE"] = "webcam"
            tui.step("Câmera", f"câmera IP {ip} não respondeu → usando a webcam", "warn")
            return
        os.environ["CAMERA_MODE"] = "rtsp"
        tui.step("Câmera", f"câmera IP {ip} não respondeu ainda (tenta reconectar sozinho)", "warn")
        return
    os.environ["CAMERA_MODE"] = "webcam"
    ok = _webcam_available(index)
    tui.step("Câmera", f"webcam {index}" if ok else f"webcam {index} (sem imagem ainda)", "ok" if ok else "warn")


def _check_proxy() -> None:
    """Com HTTPS_PROXY no realguard.env, confere se o proxy (notebook mestre) está aberto."""
    from config import console as tui
    url = (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "").strip()
    if not url:
        return
    p = setup_wizard.proxy_parts(url)
    where = f"{p.get('ip')}:{p.get('port')}" if p else url
    try:
        import socket
        with socket.create_connection((p["ip"], int(p["port"])), timeout=3):
            pass
        tui.step("Internet", f"pelo proxy {where}")
    except Exception:
        tui.step("Internet", f"proxy {where} não responde: Telegram e assistente sem internet", "warn")
        tui.out(f"    Abra o proxy no notebook mestre, ou escolha \"Direta\" em: python main.py --setup")


def _apply_fair_mode() -> None:
    """
    MODO_FEIRA=true: deixa o banco pronto para a demonstração, sem clicar em nada. Idempotente.
      * sábado e domingo passam a ser dias de aula (a feira é num sábado);
      * intervalo mínimo entre entrada e saída de 2 min (o visitante passa várias vezes);
      * simulação ao vivo dos alunos fictícios ligada (nada fictício vai para o Telegram).
    """
    from config import console as tui
    if not _truthy("MODO_FEIRA"):
        return
    try:
        from app import simulation
        from database import dados_ficticios, school
        # Feira sem alunos fictícios (banco já tinha alunos reais, ou foram removidos): cria agora,
        # senão a simulação ao vivo não tem quem simular e o painel fica parado.
        if dados_ficticios.auto_enabled() and not dados_ficticios.get_state()["registros"]["alunos"]:
            tui.step("Dados fictícios", "modo feira: criando 120 alunos e 90 dias de demonstração" + tui.G["dots"], "wait")
            try:
                info = dados_ficticios.generate(quiet=True)
                tui.step("Dados fictícios", f"criados ({info['de'][8:10]}/{info['de'][5:7]} a "
                                            f"{info['ate'][8:10]}/{info['ate'][5:7]})")
            except Exception as e:
                tui.step("Dados fictícios", f"não criados ({e}): Administração → Dados fictícios", "warn")
        cur = school.get_settings()
        days = sorted({int(x) for x in str(cur.get("school_days") or "").split(",") if x.strip().isdigit()} | {6, 7})
        gap = os.getenv("FEIRA_INTERVALO_MIN", "2").strip() or "2"
        school.update_settings({"school_days": ",".join(map(str, days)), "auto_exit_min_minutes": gap})
        st = simulation.get_state()
        if not st.get("ligada"):
            from datetime import datetime
            simulation.set_state(ligada=True, ate=datetime.now().strftime(simulation.FMT))
        tui.step("Modo feira", f"fim de semana letivo {tui.G['dot']} intervalo {gap} min {tui.G['dot']} simulação ao vivo")
    except Exception as e:
        tui.step("Modo feira", f"não aplicado ({e})", "warn")


# ---------------------------------------------------------------- servidor
def port_free(host: str, port: int) -> bool:
    """A porta está livre neste endereço? (testa do mesmo jeito que o servidor vai abrir)."""
    import socket
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)   # Windows: não "divide" a porta
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)          # igual ao uvicorn no Linux
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, port: int, tries: int = 20):
    """Primeira porta livre a partir de `port` (ex.: outro grupo já usa a 8000 na mesma AIBOX)."""
    for p in range(port, min(port + tries, 65536)):
        if port_free(host, p):
            return p
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="RealGuard")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--arena", action="store_true", help="Apresentação Arena: sala de aula")
    modes.add_argument("--complete", action="store_true", help="RealGuard completo")
    parser.add_argument("--no-browser", action="store_true", help="Não abrir navegador automaticamente")
    parser.add_argument("--host", default=None, help="Endereço de escuta (padrão: API_HOST do realguard.env)")
    parser.add_argument("--port", type=int, default=None, help="Porta (padrão: API_PORT do realguard.env)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--setup", action="store_true", help="Perguntar webcam/RTSP e credenciais antes de iniciar")
    group.add_argument("--no-setup", action="store_true", help="Não perguntar nada; usar o realguard.env como está")
    admin = parser.add_argument_group("administração (não sobem o sistema)")
    admin.add_argument("--criar-admin", action="store_true", help="Criar um usuário administrador")
    admin.add_argument("--redefinir-senha", metavar="USUARIO", help="Definir nova senha para um usuário")
    admin.add_argument("--backup", action="store_true", help="Fazer backup agora (banco + fotos dos alertas)")
    admin.add_argument("--listar-backups", action="store_true", help="Listar os backups")
    admin.add_argument("--restaurar", metavar="ARQUIVO", help="Restaurar um backup (nome do .zip em data/backups)")
    admin.add_argument("--remover-ficticios", action="store_true",
                       help="Apagar só os dados fictícios (backup automático antes)")
    args = parser.parse_args()

    if args.criar_admin:
        sys.exit(cmd_create_admin())
    if args.redefinir_senha:
        sys.exit(cmd_reset_password(args.redefinir_senha))
    if args.backup:
        sys.exit(cmd_backup())
    if args.listar_backups:
        sys.exit(cmd_list_backups())
    if args.restaurar:
        sys.exit(cmd_restore(args.restaurar))
    if args.remover_ficticios:
        sys.exit(cmd_remove_sample())

    from config import console as tui
    t0 = time.time()
    if sys.stdin and sys.stdin.isatty() and not args.no_setup:
        tui.section("PORTUGUÊS  ·  ENGLISH  ·  ESPAÑOL")
        tui.options([("1", "🇧🇷 Português (Brasil)"), ("2", "🇺🇸 English"), ("3", "🇪🇸 Español")])
        try:
            choice = tui.ask("1-3", "1", validate=lambda v: None if v in ("1", "2", "3") else "1 / 2 / 3")
        except tui.Cancelled:
            return
        os.environ["REALGUARD_UI_LOCALE"] = {"1": "pt-BR", "2": "en", "3": "es"}[choice]
    else:
        from config.ui_locale import current
        os.environ["REALGUARD_UI_LOCALE"] = current()
    tui.banner("Challenge DATEN × FIAP " + tui.G["dot"] + " NEXT 2026")

    arena_mode = args.arena or (not args.complete and os.getenv("REALGUARD_START_MODE", "complete") == "arena")
    if not args.arena and not args.complete and not args.no_setup and sys.stdin and sys.stdin.isatty():
        tui.section("Apresentação")
        tui.options([("1", "RealGuard completo — apresentação nas mesas"), ("2", "Arena — monitoramento da sala de aula")])
        try:
            arena_mode = tui.ask("Modo de execução", "1", validate=lambda v: None if v in ("1", "2") else "Escolha 1 ou 2.") == "2"
        except tui.Cancelled:
            return
    os.environ["REALGUARD_ARENA"] = "1" if arena_mode else "0"
    os.environ["REALGUARD_OPEN_BROWSER"] = "0" if args.no_browser else "1"
    if arena_mode:
        os.environ["RELOGIO_AJUSTE_MIN"] = "0"
        tui.step("Arena", "Sala inteira · relógio da sessão começa às 19h20")

    # Perguntas no terminal ANTES de qualquer coisa ler a configuração
    # (o motor lê as variáveis de ambiente ao ser importado, mais abaixo).
    if setup_wizard.should_run(force=args.setup, disabled=args.no_setup):
        try:
            setup_wizard.run(
                settings_module.ENV_FILE,
                example_path=os.path.join(settings_module.CONFIG_DIR, "realguard.env.example"),
            )
        except setup_wizard.SetupAborted:
            tui.step("Configuração", "cancelada. Nada foi iniciado.", "bad")
            sys.exit(1)

    if not args.worker:
        # O processo pai preserva o terminal da IA BOX e reinicia só o servidor.
        # No Windows ele é executado por pythonw, sem janela de terminal.
        if sys.stdin and sys.stdin.isatty() and not args.no_setup:
            setup_wizard.update_env_file(settings_module.ENV_FILE, {
                "REALGUARD_START_MODE": "arena" if arena_mode else "complete",
                "REALGUARD_UI_LOCALE": os.environ["REALGUARD_UI_LOCALE"],
            })
            _prepare_db()
            from database import access
            if access.count_users(active_admins_only=True) == 0:
                tui.step("Acesso", "nenhum administrador cadastrado", "warn")
                if tui.ask_yes("Criar o administrador agora?", True):
                    cmd_create_admin()
        from app.lifecycle import supervise
        sys.exit(supervise("arena" if arena_mode else "complete", args.no_browser, args.host, args.port))

    tui.section("Inicialização")
    env_ok = os.path.isfile(settings_module.ENV_FILE)
    tui.step("Configuração", "config/realguard.env" if env_ok else "padrões (sem config/realguard.env)",
             "ok" if env_ok else "warn")
    from datetime import datetime as _dt
    tz = settings_module.apply_timezone()
    clk = settings_module.clock_info()
    if clk["ajuste_min"] or clk["ajuste_dias"]:
        tui.step("Relógio", f"horário da demonstração {clk['agora']} {tui.G['dot']} real {clk['real']} "
                            f"(Administração → Dados fictícios)", "warn")
    else:
        tui.step("Relógio", f"{_dt.now():%d/%m/%Y %H:%M} {tui.G['dot']} fuso {tz} "
                            f"(UTC{time.strftime('%z')[:3]}:{time.strftime('%z')[3:]})")
    _check_proxy()                  # HTTPS_PROXY ligado: avisa se o proxy do mestre não responde
    _resolve_camera_auto()          # CAMERA_MODE=auto: câmera IP se responder; senão a webcam
    settings = settings_module.Settings()
    host = args.host or settings.api_host
    port = args.port or settings.api_port
    if not port_free(host, port):
        if args.port:     # porta pedida na linha de comando: não troca escondido
            tui.step("Porta", f"{port} já está em uso neste computador", "bad")
            tui.out(f"    Escolha outra: python main.py --port {port + 1}")
            sys.exit(1)
        free = pick_port(host, port + 1)
        if free is None:
            tui.step("Porta", f"{port} a {port + 20} todas em uso", "bad")
            tui.out("    Use: python main.py --port <número livre>")
            sys.exit(1)
        tui.step("Porta", f"{port} ocupada (outro grupo ou programa) → usando {free}", "warn")
        port = free
    else:
        tui.step("Porta", str(port))
    ssl = {}
    if settings.https_enabled:
        missing = [p for p in (settings.ssl_certfile, settings.ssl_keyfile) if not os.path.isfile(p)]
        if missing:
            tui.step("HTTPS", f"arquivo não encontrado: {', '.join(missing)}", "bad")
            sys.exit(1)
        ssl = {"ssl_certfile": settings.ssl_certfile, "ssl_keyfile": settings.ssl_keyfile}
    scheme = "https" if ssl else "http"
    # Link da tela de Ocorrências nos avisos do Telegram (só abre de dentro da rede da escola).
    if not os.getenv("REALGUARD_URL"):
        ips = settings_module.lan_addresses() if host == "0.0.0.0" else [host]
        if ips:
            os.environ["REALGUARD_URL"] = f"{scheme}://{ips[0]}:{port}"

    try:
        _prepare_db()
    except Exception as e:
        tui.step("Banco de dados", str(e), "bad")
        sys.exit(1)
    from database.migrations import LATEST_VERSION
    tui.step("Banco de dados", f"pronto (versão {LATEST_VERSION})")
    if not arena_mode:
        _seed_sample_data()
        _apply_fair_mode()
    from database import access
    no_admin = access.count_users(active_admins_only=True) == 0
    if no_admin and sys.stdin and sys.stdin.isatty() and not args.no_setup:
        # Primeiro uso: oferece criar o administrador aqui mesmo, sem outro terminal.
        tui.step("Acesso", "nenhum administrador cadastrado", "warn")
        tui.out()
        try:
            create = tui.ask_yes("Criar o administrador agora?", True)
        except tui.Cancelled:
            create = False
        if create and cmd_create_admin() == 0:
            no_admin = False
    users = access.count_users()
    tui.step("Acesso", "nenhum administrador: python main.py --criar-admin" if no_admin
             else tui.pluralize(users, "usuário") + " cadastrado" + ("" if users == 1 else "s"),
             "warn" if no_admin else "ok")

    tui.step("Servidor e IA", "carregando modelos" + tui.G["dots"], "wait")
    tui.hold()       # avisos da subida (modelos, câmera) entram resumidos no painel
    import uvicorn
    from api.main import LOG_FILE, app

    import json
    address_file = os.path.join(settings_module.BASE_DIR, "logs", "active_server.json")
    os.makedirs(os.path.dirname(address_file), exist_ok=True)
    with open(address_file, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "url": f"{scheme}://127.0.0.1:{port}"}, stream)

    threading.Thread(target=_report_when_ready, args=(host, port, scheme, t0, no_admin, LOG_FILE),
                     daemon=True, name="startup-report").start()
    try:
        # Sem log_config: as mensagens do servidor passam pelo mesmo filtro (terminal enxuto,
        # arquivo completo). Sem log de acesso: o painel consulta o servidor a cada 4 s.
        from app.server import RealGuardServer
        from app.lifecycle import register_server
        server = RealGuardServer(uvicorn.Config(app, host=host, port=port, log_level="info", log_config=None,
                    timeout_graceful_shutdown=5,
                    access_log=os.getenv("LOG_ACCESS", "").lower() in ("1", "true", "sim"), **ssl))
        register_server(server)
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        tui.out()
        tui.out(tui.c("  RealGuard encerrado.", "grey"))
        try:
            with open(address_file, "r", encoding="utf-8") as stream:
                if json.load(stream).get("pid") == os.getpid():
                    os.remove(address_file)
        except (OSError, ValueError):
            pass
    from app.lifecycle import action, RESTART_EXIT_CODE
    if action() == "restart":
        sys.exit(RESTART_EXIT_CODE)


# ---------------------------------------------------------------- painel "pronto"
def _wait(cond, timeout: float, every: float = 0.25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(every)
    return False


def _listening(host: str, port: int) -> bool:
    import socket
    target = "127.0.0.1" if host in ("0.0.0.0", "") else ("::1" if host == "::" else host)
    try:
        with socket.create_connection((target, port), timeout=0.5):
            return True
    except OSError:
        return False


def _report_when_ready(host: str, port: int, scheme: str, t0: float, no_admin: bool, log_file: str) -> None:
    """Espera o servidor e a IA ficarem prontos e mostra o painel de status (uma vez)."""
    from config import console as tui
    try:
        if not _wait(lambda: _listening(host, port), 60):
            tui.release()
            return
        if os.getenv("REALGUARD_OPEN_BROWSER", "1") == "1":
            import webbrowser
            target = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            if os.name == "nt" or os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"):
                webbrowser.open(f"{scheme}://{target}:{port}", new=0, autoraise=True)
        from app.engine import STATE_STARTING, engine
        _wait(lambda: engine.state != STATE_STARTING, 120)
        # dá alguns segundos para o primeiro quadro da câmera chegar
        _wait(lambda: bool(engine.pipeline and engine.pipeline.get_status().get("camera_online")), 8)
        tui.panel(f"RealGuard pronto em {tui.fmt_seconds(time.time() - t0)}",
                  _status_rows(engine, host, port, scheme, no_admin),
                  [f"Ctrl+C encerra {tui.G['dot']} avisos e erros aparecem abaixo",
                   "Registro completo: " + (os.path.relpath(log_file) if log_file else "sem arquivo de log")])
        # o painel já resume IA e câmera; o resto (se houver) aparece agora
        tui.release(("app.engine", "app.recognition", "app.detection", "app.camera", "app.pipeline"))
    except Exception as e:      # o painel nunca derruba o sistema
        tui.release()
        tui.step("Status", f"não consegui montar o painel ({e})", "warn")


def _status_rows(engine, host: str, port: int, scheme: str, no_admin: bool):
    rows = []
    # Painel (endereços)
    if host == "0.0.0.0":
        ips = settings_module.lan_addresses()
        links = [f"{scheme}://{ip}:{port}" for ip in ips] or [f"{scheme}://<IP-desta-máquina>:{port}"]
        rows.append(("ok", "Painel", "\n".join(links + [f"{scheme}://127.0.0.1:{port}  (nesta máquina)"])))
    else:
        extra = ("\nsó neste computador; para outros na rede: python main.py --host 0.0.0.0"
                 if host in ("127.0.0.1", "localhost") else "")
        rows.append(("ok", "Painel", f"{scheme}://{host}:{port}{extra}"))

    # IA
    st = engine.status()
    if engine.state == "running":
        rows.append(("ok", "Detecção", f"pessoas (YOLOv8n) {_ms(st.get('ms_deteccao'))}".strip()))
        if st.get("reconhecimento_facial"):
            faces = st.get("rostos_na_base", 0)
            rows.append(("ok" if faces else "info", "Rostos",
                         f"reconhecimento ligado {chr(183)} {faces} aluno(s) com rosto cadastrado"))
        else:
            why = (engine.notice or "").replace("Reconhecimento facial desativado: ", "")
            rows.append(("warn", "Rostos", "reconhecimento facial desligado (só detecção de pessoas)"
                         + (f"\n{why[:90]}" if why else "")))
    else:
        rows.append(("bad", "IA", f"não iniciou: {(engine.error or engine.state)[:90]}\n"
                                  "o sistema tenta de novo sozinho; detalhes no registro"))

    # Câmera
    cam_name = engine.settings.camera_name
    source = engine.settings.get_safe_summary().get("CAMERA_SOURCE", "")
    src = f"webcam {source}" if str(source).isdigit() else source
    if st.get("camera_online"):
        rows.append(("ok", "Câmera", f"{cam_name} {chr(183)} {st.get('resolucao') or ''} {st.get('fps_camera', 0)} fps"
                                     f"\n{src}"))
    else:
        rows.append(("warn", "Câmera", f"{cam_name}: sem imagem ainda (tenta reconectar sozinho)\n{src}"))

    # Telegram
    def bot(n):
        name = n.get_bot_username(block=False)
        return f"@{name}" if name else "configurado"
    if engine.notifier.enabled:
        sec = bot(engine.sec_notifier) if engine.security_separate else "pelo bot dos responsáveis"
        on = "" if engine.guardians_enabled else "  (avisos aos responsáveis DESLIGADOS)"
        rows.append(("ok" if on == "" else "warn", "Telegram", f"responsáveis {bot(engine.notifier)}{on}"
                                                                f"\nsegurança {sec}"))
    else:
        rows.append(("warn", "Telegram", "não configurado: presença registra, sem avisos\n"
                                         "Escola → Integrações → Telegram"))

    # Assistente virtual
    try:
        from app.assistant import service as ast
        key = ast.gemini_key()[0]
        sup = bool(ast.support_chat()[0])
        if not ast.is_enabled():
            rows.append(("info", "Assistente", "desligado (só perguntas frequentes)"))
        elif key:
            rows.append(("ok", "Assistente", f"pronto ({ast.model_name()})"
                         + ("" if sup else "\nfalta o Telegram do suporte: Escola → Assistente virtual")))
        else:
            rows.append(("warn", "Assistente", "sem chave do Gemini: só perguntas frequentes\n"
                                               "Escola → Assistente virtual"))
    except Exception:
        pass

    if _truthy("MODO_FEIRA"):
        rows.append(("info", "Modo feira", "fim de semana letivo, intervalo curto e simulação ao vivo"))

    # Acesso
    if no_admin:
        rows.append(("bad", "Acesso", "nenhum administrador: python main.py --criar-admin"))
    return rows


def _ms(value) -> str:
    return f"{chr(183)} {value:.0f} ms" if isinstance(value, (int, float)) and value else ""


if __name__ == "__main__":
    main()
