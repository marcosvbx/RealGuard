"""
Terminal do RealGuard: abertura, etapas de inicialização, painel de status e
mensagens enxutas.

* Cores só quando o terminal suporta (e NO_COLOR não está definido). No
  Windows, liga o modo ANSI do console; se não der, sai sem cor.
* Símbolos Unicode só quando a codificação do terminal aguenta; senão, ASCII.
* O terminal mostra só avisos e erros, curtos e sem repetição: a mesma
  mensagem dentro de REPEAT_WINDOW segundos é segurada e depois resumida
  ("repetiu N vezes"). O log completo continua em logs/realguard.log.
"""

import logging
import os
import re
import sys
import threading
import time
from typing import Iterable, Optional, Tuple

REPEAT_WINDOW = 60.0


# ================================================================== capacidade do terminal
def _enable_windows_ansi() -> bool:
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def _detect_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.getenv("NO_COLOR") or os.getenv("REALGUARD_COLOR", "").lower() in ("0", "false", "off", "nao", "não"):
        return False
    if os.getenv("REALGUARD_COLOR", "").lower() in ("1", "true", "on", "sim"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        return _enable_windows_ansi()
    return os.getenv("TERM", "") != "dumb"


def _detect_unicode(stream=None) -> bool:
    enc = getattr(stream or sys.stdout, "encoding", "") or ""
    try:
        "✓✗▸━•⚠·…".encode(enc)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _detect_truecolor() -> bool:
    """Cores exatas (24 bits) na maioria dos terminais atuais; 256 cores onde isso não existe."""
    forced = os.getenv("REALGUARD_COLOR", "").lower()
    if forced in ("256", "8bit"):
        return False
    if forced in ("24bit", "truecolor"):
        return True
    if os.getenv("COLORTERM", "").lower() in ("truecolor", "24bit") or os.getenv("WT_SESSION"):
        return True
    if os.name == "nt":
        return True                      # console do Windows 10+ com modo ANSI ligado
    if os.getenv("TERM_PROGRAM") == "Apple_Terminal" or os.getenv("TERM", "") in ("linux", "screen", "vt100"):
        return False
    return True                          # SSH a partir do Windows Terminal / PowerShell / VS Code


COLOR = _detect_color()
UNICODE = _detect_unicode()
TRUECOLOR = _detect_truecolor()

G = {  # símbolos
    "ok": "✓" if UNICODE else "OK",
    "warn": "!" ,
    "bad": "✗" if UNICODE else "X",
    "info": "•" if UNICODE else "-",
    "step": "▸" if UNICODE else ">",
    "line": "━" if UNICODE else "=",
    "dot": "·" if UNICODE else "-",
    "dots": "…" if UNICODE else "...",
}

# Paleta do dashboard (web/index.html, tema escuro) e da identidade visual. Dourado é só acento da marca
# (slogan e a pergunta ativa), nunca estado: estados usam verde / amarelo / vermelho / azul, como no painel.
PALETTE = {                     # nome: (hex do dashboard, aproximação em 256 cores)
    "white": ("#eef2f8", 255),  # --ink: texto principal
    "soft": ("#b3bfd2", 250),   # --ink-2: valores e texto secundário
    "grey": ("#7f8ca3", 103),   # --muted: dicas, pontos, horários
    "line": ("#283e5c", 60),    # ardósia da marca: réguas
    "blue": ("#3987e5", 68),    # --accent: interface (opções, em andamento)
    "green": ("#22b04a", 35),   # --good
    "yellow": ("#fab219", 214), # --warn
    "red": ("#e5484d", 167),    # --bad
    "gold": ("#d6b25e", 179),   # --gold: acento da marca
    "navy": ("#0b2447", 17),    # marinho da logo
}


def _fg(hex_color: str, idx: int) -> str:
    if not TRUECOLOR:
        return f"38;5;{idx}"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"38;2;{r};{g};{b}"


_CODES = {"reset": "0", "bold": "1", "dim": "2", **{k: _fg(*v) for k, v in PALETTE.items()}}


def c(text: str, *styles: str) -> str:
    if not COLOR or not styles:
        return text
    return "".join(f"\033[{_CODES[s]}m" for s in styles) + text + "\033[0m"


_ANSI = re.compile(r"\033\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


_LAST_BLANK = [True]       # a última linha impressa estava em branco? (evita duas linhas vazias seguidas)
IND = "    "               # recuo do conteúdo das seções (títulos ficam na coluna 2)


def out(text: str = "") -> None:
    from config.ui_locale import translate
    text = translate(text)
    blank = not _ANSI.sub("", text).strip()
    if blank and _LAST_BLANK[0]:
        return
    _LAST_BLANK[0] = blank
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", "") or "ascii"
        print(text.replace("→", "->").encode(enc, "replace").decode(enc), flush=True)


# ================================================================== abertura e etapas
WIDTH = 76

_LOGO_UNICODE = [
    '██████╗ ███████╗ █████╗ ██╗      ██████╗ ██╗   ██╗ █████╗ ██████╗ ██████╗',
    '██╔══██╗██╔════╝██╔══██╗██║     ██╔════╝ ██║   ██║██╔══██╗██╔══██╗██╔══██╗',
    '██████╔╝█████╗  ███████║██║     ██║  ███╗██║   ██║███████║██████╔╝██║  ██║',
    '██╔══██╗██╔══╝  ██╔══██║██║     ██║   ██║██║   ██║██╔══██║██╔══██╗██║  ██║',
    '██║  ██║███████╗██║  ██║███████╗╚██████╔╝╚██████╔╝██║  ██║██║  ██║██████╔╝',
    '╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝',
]
_LOGO_ASCII = [
    ' ___ ___   _   _    ___ _   _  _   ___ ___',
    '| _ \\ __| /_\\ | |  / __| | | |/_\\ | _ \\   \\',
    '|   / _| / _ \\| |_| (_ | |_| / _ \\|   / |) |',
    '|_|_\\___/_/ \\_\\____\\___|\\___/_/ \\_\\_|_\\___/',
]


SPLIT_LOGO = 32       # coluna onde "GUARD" começa no logo grande (REAL leve + GUARD forte, como na marca)
SLOGAN = "PROTEÇÃO REAL, EM TEMPO REAL"


def wordmark(text: str = "RealGuard") -> str:
    """RealGuard como na logo: "Real" leve, "Guard" forte."""
    if not text.startswith("RealGuard"):
        return c(text, "white", "bold")
    return c("Real", "soft") + c("Guard", "white", "bold") + c(text[9:], "white")


def rule(color: str = "line") -> None:
    out(c(G["line"] * WIDTH, color))


def banner(subtitle: str = "") -> None:
    out()
    if UNICODE:
        for line in _LOGO_UNICODE:
            out("  " + c(line[:SPLIT_LOGO], "soft") + c(line[SPLIT_LOGO:], "white", "bold"))
    else:
        for line in _LOGO_ASCII:
            out(c("  " + line, "white", "bold"))
    out(c("  " + SLOGAN, "gold"))
    out()
    out(c("  Controle de acesso, presença e segurança escolar " + G["dot"] + " Edge AI", "grey"))
    if subtitle:
        out(c("  " + subtitle, "grey"))
    out()


def step(label: str, value: str = "", state: str = "ok") -> None:
    """Uma linha de etapa: ▸ Rótulo ........ valor."""
    icon = {"ok": c(G["ok"], "green"), "warn": c(G["warn"], "yellow", "bold"), "bad": c(G["bad"], "red"),
            "info": c(G["info"], "blue"), "wait": c(G["step"], "blue")}[state]
    dots = "." * max(2, 24 - len(label))
    color = {"ok": "soft", "warn": "yellow", "bad": "red", "info": "grey", "wait": "grey"}[state]
    out(f"{IND}{icon} {c(label, 'white')} {c(dots, 'line')} {c(value, color)}")


def section(title: str) -> None:
    from config.ui_locale import translate
    out()
    out(c("  " + translate(title).upper(), "white", "bold"))
    out()


def panel(title: str, rows: Iterable[Tuple[str, str, str]], footer: Iterable[str] = ()) -> None:
    """Painel final: (estado, rótulo, valor) por linha."""
    out()
    rule()
    out("  " + wordmark(title))
    rule()
    for state, label, value in rows:
        icon = {"ok": c(G["ok"], "green"), "warn": c(G["warn"], "yellow", "bold"), "bad": c(G["bad"], "red"),
                "info": c(G["info"], "blue")}[state]
        color = {"ok": "soft", "warn": "yellow", "bad": "red", "info": "grey"}[state]
        first, *rest = (value or "").split("\n")
        out(f"  {icon} {c(f'{label:<15}', 'white')}{c(first, color)}")
        for extra in rest:
            out(f"    {'':<15}{c(extra, 'grey')}")
    rule()
    for line in footer:
        out(c("  " + line, "grey"))
    out()


# ================================================================== logs no terminal
_LEVEL_STYLE = {
    logging.DEBUG: ("grey",), logging.INFO: ("blue",), logging.WARNING: ("yellow",),
    logging.ERROR: ("red",), logging.CRITICAL: ("red", "bold"),
}


class ConsoleFormatter(logging.Formatter):
    """'12:04:05 ! mensagem' — curto, colorido pelo nível, sem nome de módulo nem data."""

    def format(self, record: logging.LogRecord) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(record.created))
        icon = G["bad"] if record.levelno >= logging.ERROR else G["warn"] if record.levelno >= logging.WARNING \
            else G["info"]
        msg = record.getMessage()
        msg = re.sub(r"^\[[^\]]{1,40}\]\s*", "", msg)          # "[Camera_01] Câmera..." -> "Câmera..."
        repeats = getattr(record, "rg_repeats", 0)
        if repeats:
            msg += c(f"  (repetiu {repeats}x no último minuto)", "grey")
        text = f"  {c(t, 'grey')} {c(icon, *_LEVEL_STYLE.get(record.levelno, ()))} {msg}"
        if record.exc_info and record.levelno >= logging.ERROR:
            exc = record.exc_info[1]
            text += c(f"  [{type(exc).__name__}: {exc}]", "grey") if exc else ""
        enc = getattr(sys.stderr, "encoding", "") or "utf-8"
        try:
            text.encode(enc)
        except (LookupError, UnicodeEncodeError):       # terminal antigo (ex.: cp1252): troca o que não cabe
            text = text.encode(enc, "replace").decode(enc) if enc else text
        return text


class RepeatFilter(logging.Filter):
    """
    Segura mensagens iguais (ignorando números) por REPEAT_WINDOW segundos.
    Quando a janela vence e a mensagem volta, ela sai com "repetiu N vezes".
    """

    def __init__(self, window: float = REPEAT_WINDOW):
        super().__init__()
        self.window = window
        self._seen = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(record: logging.LogRecord) -> str:
        return f"{record.name}|{record.levelno}|" + re.sub(r"\d+", "#", record.getMessage())

    def filter(self, record: logging.LogRecord) -> bool:
        now = record.created
        key = self._key(record)
        with self._lock:
            first, count = self._seen.get(key, (0.0, 0))
            if now - first < self.window:
                self._seen[key] = (first, count + 1)
                return False
            self._seen[key] = (now, 0)
            if len(self._seen) > 500:
                self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.window}
        record.rg_repeats = count
        return True


def console_level() -> int:
    name = os.getenv("LOG_CONSOLE_LEVEL", "WARNING").strip().upper()
    return getattr(logging, name, logging.WARNING)


class ConsoleHandler(logging.StreamHandler):
    """
    Durante a inicialização (hold()), guarda as mensagens em vez de imprimir no meio das etapas.
    release() imprime as que o painel de status não cobre.
    """
    held = None          # lista quando segurando; None = imprime direto
    _lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with ConsoleHandler._lock:
            if ConsoleHandler.held is not None:
                ConsoleHandler.held.append((self, record))
                return
        super().emit(record)


def hold() -> None:
    with ConsoleHandler._lock:
        ConsoleHandler.held = []


def release(skip_loggers: Iterable[str] = ()) -> None:
    """Para de segurar e imprime o que ficou guardado (menos o dos loggers que o painel já resume)."""
    skip = tuple(skip_loggers)
    with ConsoleHandler._lock:
        held, ConsoleHandler.held = ConsoleHandler.held or [], None
    for handler, record in held:
        if not record.name.startswith(skip):
            logging.StreamHandler.emit(handler, record)


def make_console_handler(stream=None) -> logging.Handler:
    handler = ConsoleHandler(stream or sys.stderr)
    handler.setLevel(console_level())
    handler.setFormatter(ConsoleFormatter())
    handler.addFilter(RepeatFilter())
    handler.rg_console = True        # marca: é o handler do terminal
    return handler


def fmt_seconds(sec: float) -> str:
    return f"{sec:.1f} s".replace(".", ",")


def pluralize(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ================================================================== perguntas no terminal
# Tudo com o mesmo recuo: título da seção na coluna 2, textos e opções na coluna 4,
# perguntas com "›" e o valor padrão entre parênteses. Ctrl+C / fim da entrada
# viram Cancelled, para quem chama decidir o que fazer.
class Cancelled(Exception):
    pass


def header(title: str, subtitle: str = "") -> None:
    from config.ui_locale import translate
    out()
    out(c("  " + translate(title).upper(), "white", "bold"))
    if subtitle:
        for line in subtitle.split("\n"):
            out(c(IND + line, "grey"))
    out()


def subheader(title: str, hint: str = "") -> None:
    """Bloco dentro de uma seção: título em negrito e, embaixo, a dica em cinza."""
    out()
    out(IND + c(title, "white", "bold"))
    if hint:
        out(c(IND + hint, "grey"))
    out()


def say(text: str, state: str = "", indent: int = 4) -> None:
    """Mensagem: ok / warn / bad / info (com símbolo) ou "" (texto simples)."""
    pad = " " * indent
    if not state:
        for line in text.split("\n"):
            out(pad + line)
        return
    icon = {"ok": c(G["ok"], "green"), "warn": c(G["warn"], "yellow", "bold"), "bad": c(G["bad"], "red"),
            "info": c(G["info"], "blue")}[state]
    first, *rest = text.split("\n")
    out(f"{pad}{icon} {first}")
    for line in rest:
        out(c(f"{pad}  {line}", "grey"))


def options(items: Iterable[Tuple[str, str]]) -> None:
    """Lista de escolhas alinhada: [(tecla, descrição), ...]."""
    items = list(items)
    width = max(len(k) for k, _ in items) + 3
    for key, label in items:
        out(f"    {c(key.ljust(width), 'blue', 'bold')}{c(label, 'soft')}")
    out()


def _prompt(label: str, default: str = "", hint: str = "") -> str:
    from config.ui_locale import translate
    label, hint = translate(label), translate(hint)
    extra = f" {c('(' + default + ')', 'grey')}" if default else ""
    extra += f" {c('[' + hint + ']', 'grey')}" if hint else ""
    _LAST_BLANK[0] = False     # a linha da pergunta (e da resposta) conta como texto: a próxima linha vazia sai
    return f"{IND}{c('›', 'gold', 'bold')} {label}{extra}: "


def ask(label: str, default: str = "", validate=None) -> str:
    """Pergunta com valor padrão (Enter aceita). validate(valor) -> mensagem de erro ou None."""
    while True:
        try:
            value = input(_prompt(label, default)).strip()
        except (EOFError, KeyboardInterrupt):
            out()
            raise Cancelled()
        value = value or default
        problem = validate(value) if validate else None
        if not problem:
            return value
        say(problem, "bad", indent=6)


def ask_yes(label: str, default: bool = True) -> bool:
    from config.ui_locale import current
    hint = ("Y/n" if default else "y/N") if current() == "en" else ("S/n" if default else "s/N")
    while True:
        try:
            value = input(_prompt(label, hint=hint)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            out()
            raise Cancelled()
        if not value:
            return default
        if value in ("s", "sim", "y", "yes"):
            return True
        if value in ("n", "nao", "não", "no"):
            return False
        say("Responda s (sim) ou n (não).", "bad", indent=6)


def ask_secret(label: str, keep_hint: bool = False) -> str:
    """Senha sem eco. Com keep_hint, Enter vazio devolve "" (quem chama mantém a atual)."""
    import getpass
    hint = "Enter mantém a atual" if keep_hint else "não aparece na tela"
    try:
        return getpass.getpass(_prompt(label, hint=hint))
    except (EOFError, KeyboardInterrupt):
        out()
        raise Cancelled()
