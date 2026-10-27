"""
Backup e restauração do RealGuard (banco + fotos dos alertas).

* Banco copiado com a API de backup online do SQLite: consistente mesmo com
  a IA gravando presenças no mesmo instante (não é uma cópia de arquivo).
* Um backup é um .zip com `realguard.db`, as fotos de `events/` e um
  `manifest.json`. Fica em data/backups (fora do Git, permissão 600):
  contém vetores faciais, então é dado pessoal sensível (LGPD).
* Rotação: guarda os BACKUP_KEEP mais recentes de cada tipo.
* Restaurar faz antes um backup "pre-restauracao" do estado atual.
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.settings import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

KINDS = ("auto", "manual", "pre-restauracao")
NAME_RE = re.compile(r"^realguard-(\d{8}-\d{6})-(auto|manual|pre-restauracao)\.zip$")
REQUIRED_TABLES = ("students", "student_faces", "presences")


class BackupError(RuntimeError):
    pass


def db_path() -> str:
    return os.path.abspath(os.getenv("DB_PATH", DEFAULT_DB_PATH))


def data_dir() -> str:
    return os.path.dirname(db_path())


def backup_dir() -> str:
    return os.path.abspath(os.getenv("BACKUP_DIR", "") or os.path.join(data_dir(), "backups"))


def events_dir() -> str:
    return os.path.join(data_dir(), "events")


def keep_count() -> int:
    try:
        return max(1, int(os.getenv("BACKUP_KEEP", "14")))
    except ValueError:
        return 14


def _private(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _copy_db(src_path: str, dst_path: str) -> None:
    with closing(sqlite3.connect(src_path, timeout=30)) as src, closing(sqlite3.connect(dst_path)) as dst:
        src.backup(dst)


def _schema_version(path: str) -> int:
    try:
        with closing(sqlite3.connect(path)) as conn:
            return int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def create_backup(kind: str = "manual") -> Dict[str, Any]:
    if kind not in KINDS:
        raise BackupError("Tipo de backup inválido.")
    if not os.path.isfile(db_path()):
        raise BackupError("Ainda não existe banco de dados para copiar.")
    os.makedirs(backup_dir(), exist_ok=True)
    for _attempt in range(3):   # dois backups do mesmo tipo no mesmo segundo: espera o próximo
        name = f"realguard-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}.zip"
        final = os.path.join(backup_dir(), name)
        if not os.path.exists(final):
            break
        time.sleep(1.0)
    else:
        raise BackupError("Já existe um backup com este horário. Tente de novo em instantes.")
    with tempfile.TemporaryDirectory(dir=backup_dir()) as tmp:
        tmp_db = os.path.join(tmp, "realguard.db")
        _copy_db(db_path(), tmp_db)
        photos = 0
        part = final + ".part"
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, "realguard.db")
            if os.path.isdir(events_dir()):
                for fname in sorted(os.listdir(events_dir())):
                    if fname.endswith(".jpg"):
                        z.write(os.path.join(events_dir(), fname), "events/" + fname, compress_type=zipfile.ZIP_STORED)
                        photos += 1
            z.writestr("manifest.json", json.dumps({
                "sistema": "RealGuard", "criado_em": datetime.now().isoformat(timespec="seconds"),
                "tipo": kind, "versao_schema": _schema_version(tmp_db), "fotos_alertas": photos,
            }, ensure_ascii=False, indent=2))
        os.replace(part, final)
    _private(final)
    rotate()
    logger.info(f"Backup criado: {name} ({photos} foto(s) de alerta).")
    return describe(name)


def describe(name: str) -> Dict[str, Any]:
    m = NAME_RE.match(name)
    path = os.path.join(backup_dir(), name)
    created = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else None
    return {"nome": name, "tipo": m.group(2) if m else "?", "tamanho": os.path.getsize(path),
            "criado_em": created.strftime("%Y-%m-%d %H:%M:%S") if created else ""}


def list_backups() -> List[Dict[str, Any]]:
    if not os.path.isdir(backup_dir()):
        return []
    names = [n for n in os.listdir(backup_dir()) if NAME_RE.match(n)]
    return [describe(n) for n in sorted(names, reverse=True)]


def rotate() -> int:
    removed = 0
    by_kind: Dict[str, List[str]] = {}
    for b in list_backups():
        by_kind.setdefault(b["tipo"], []).append(b["nome"])
    for names in by_kind.values():
        for old in names[keep_count():]:   # já vem do mais novo para o mais antigo
            try:
                os.remove(os.path.join(backup_dir(), old))
                removed += 1
            except OSError as e:
                logger.warning(f"Não consegui apagar o backup antigo {old}: {e}")
    return removed


def backup_path(name: str) -> str:
    """Caminho seguro de um backup existente (nome validado: sem ../)."""
    if not NAME_RE.match(name or ""):
        raise BackupError("Nome de backup inválido.")
    path = os.path.join(backup_dir(), name)
    if not os.path.isfile(path):
        raise BackupError("Backup não encontrado.")
    return path


def latest(kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for b in list_backups():
        if kind is None or b["tipo"] == kind:
            return b
    return None


def _validate_db(path: str) -> None:
    try:
        with closing(sqlite3.connect(path)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("O banco dentro do backup está corrompido.")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as e:
        raise BackupError(f"O arquivo não é um banco do RealGuard: {e}")
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing:
        raise BackupError("O arquivo não é um backup do RealGuard (faltam tabelas: " + ", ".join(missing) + ").")


def restore_backup(name: str) -> Dict[str, Any]:
    """
    Substitui o banco e as fotos de alerta pelos do backup. Faz antes um
    backup de segurança do estado atual. O banco é escrito pela API de
    backup do SQLite (seguro com o sistema rodando).
    """
    path = backup_path(name)
    with tempfile.TemporaryDirectory(dir=backup_dir()) as tmp:
        try:
            with zipfile.ZipFile(path) as z:
                members = z.namelist()
                if "realguard.db" not in members:
                    raise BackupError("O backup não contém o banco de dados.")
                for m in members:
                    if m.startswith("/") or ".." in m.split("/"):
                        raise BackupError("Backup com caminho suspeito: restauração cancelada.")
                z.extractall(tmp)
        except zipfile.BadZipFile:
            raise BackupError("Arquivo de backup corrompido.")
        restored_db = os.path.join(tmp, "realguard.db")
        _validate_db(restored_db)

        safety = create_backup("pre-restauracao") if os.path.isfile(db_path()) else None
        _copy_db(restored_db, db_path())

        src_events = os.path.join(tmp, "events")
        if os.path.isdir(events_dir()):
            shutil.rmtree(events_dir())
        if os.path.isdir(src_events):
            shutil.copytree(src_events, events_dir())
    logger.warning(f"Backup restaurado: {name}.")
    return {"restaurado": name, "backup_de_seguranca": safety["nome"] if safety else None}
