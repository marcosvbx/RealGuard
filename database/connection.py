import logging
import os
import sqlite3
from contextlib import contextmanager

from config.settings import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)


@contextmanager
def get_db_connection():
    """
    [DECISÃO DO PROJETO] DB_PATH é lido do ambiente a cada chamada para evitar
    congelamento de constantes em tempo de importação, garantindo isolamento em testes.

    O banco é acessado por várias threads (loop de IA + API). O modo WAL e o
    busy_timeout evitam "database is locked" entre leitor e escritor.
    """
    conn = None
    db_path = os.getenv("DB_PATH", DEFAULT_DB_PATH)
    try:
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        from config.clock import datetime
        conn.create_function('rg_now', 0, lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Erro ao conectar no banco SQLite: {e}")
        raise
    finally:
        if conn:
            conn.close()
