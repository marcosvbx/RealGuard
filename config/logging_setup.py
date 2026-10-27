"""
Logs do RealGuard: terminal + arquivo com rotação (logs/realguard.log).

Terminal: só avisos e erros (LOG_CONSOLE_LEVEL, padrão WARNING), curtos e sem
repetição — ver config/console.py. Arquivo: tudo a partir de LOG_LEVEL (INFO).

Sem isso, um erro de madrugada na AIBOX some quando o terminal fecha.
O arquivo gira em LOG_MAX_MB (padrão 5 MB) e guarda LOG_BACKUPS cópias
(padrão 5): no máximo ~30 MB em disco. Credenciais nunca são logadas (a
URL da câmera sai mascarada pelo próprio Settings.get_safe_summary).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from config.settings import BASE_DIR

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_FILE_HANDLER = None


def log_dir() -> str:
    return os.path.abspath(os.getenv("LOG_DIR", "") or os.path.join(BASE_DIR, "logs"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def setup_logging() -> str:
    """Configura o logger raiz (idempotente). Devolve o caminho do arquivo de log ("" se indisponível)."""
    global _FILE_HANDLER
    root = logging.getLogger()
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    root.setLevel(level)
    if not any(getattr(h, "rg_console", False) for h in root.handlers):
        from config.console import make_console_handler
        root.addHandler(make_console_handler())
    if _FILE_HANDLER is not None:
        return _FILE_HANDLER.baseFilename
    try:
        os.makedirs(log_dir(), exist_ok=True)
        path = os.path.join(log_dir(), "realguard.log")
        handler = RotatingFileHandler(path, maxBytes=max(1, _int("LOG_MAX_MB", 5)) * 1024 * 1024,
                                      backupCount=max(1, _int("LOG_BACKUPS", 5)), encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
        _FILE_HANDLER = handler
        return path
    except OSError as e:
        logging.getLogger(__name__).warning(f"Sem log em arquivo ({e}); seguindo só no terminal.")
        return ""


def attach_server_logs() -> None:
    """Erros do servidor web (uvicorn.error) também no arquivo. O log de acesso fica fora (muito ruído)."""
    if _FILE_HANDLER is None:
        return
    lg = logging.getLogger("uvicorn.error")
    if lg.propagate and not lg.handlers:
        return          # uvicorn sem configuração própria: já chega ao arquivo pelo logger raiz
    if _FILE_HANDLER not in lg.handlers:
        lg.addHandler(_FILE_HANDLER)
