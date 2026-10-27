"""
Persistência de acesso: usuários, perfis, sessões e auditoria.

Faz parte da camada de repositório: a API nunca fala SQL direto com essas
tabelas. As chamadas são síncronas (sqlite3) e rodam no threadpool do
FastAPI, como o resto do repositório.
"""

import hashlib
import json
import logging
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.security import passwords
from app.security.permissions import (
    ALL_PERMISSIONS, ROLE_ADMIN, SYSTEM_ROLES, clean_permissions,
)
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
DT_FMT = "%Y-%m-%d %H:%M:%S"


class AccessError(ValueError):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


def _now() -> str:
    return datetime.now().strftime(DT_FMT)


def normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ================================================================== perfis
def sync_system_roles(conn: sqlite3.Connection) -> None:
    """Cria/atualiza os perfis padrão a partir do código (chamado na migração)."""
    for key, (name, desc, perms) in SYSTEM_ROLES.items():
        conn.execute(
            "INSERT INTO roles (key, name, description, is_system, permissions) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(key) DO UPDATE SET name = excluded.name, description = excluded.description, "
            "is_system = 1, permissions = excluded.permissions",
            (key, name, desc, json.dumps(list(perms))),
        )


def _role_row(row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["permissions"] = clean_permissions(json.loads(d.get("permissions") or "[]"))
    except ValueError:
        d["permissions"] = []
    d["is_system"] = bool(d.get("is_system"))
    return d


def list_roles() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.role = r.key) AS users "
            "FROM roles r ORDER BY r.is_system DESC, r.id").fetchall()
    order = {k: i for i, k in enumerate(SYSTEM_ROLES)}
    roles = [_role_row(r) for r in rows]
    roles.sort(key=lambda r: (0, order.get(r["key"], 99)) if r["is_system"] else (1, r["id"]))
    return roles


def get_role(key: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM roles WHERE key = ?", (key,)).fetchone()
    return _role_row(row) if row else None


def role_permissions(key: str) -> List[str]:
    if key == ROLE_ADMIN:
        return list(ALL_PERMISSIONS)   # admin sempre tem tudo, mesmo com catálogo novo
    role = get_role(key)
    return role["permissions"] if role else []


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:24] or "perfil"
    return f"custom-{base}-{secrets.token_hex(2)}"


def create_role(name: str, description: str, permissions: List[str]) -> Dict[str, Any]:
    name = (name or "").strip()
    if not 2 <= len(name) <= 40:
        raise AccessError("Dê um nome de 2 a 40 caracteres ao perfil.")
    with get_db_connection() as conn:
        if conn.execute("SELECT 1 FROM roles WHERE lower(name) = lower(?)", (name,)).fetchone():
            raise AccessError("Já existe um perfil com esse nome.")
        key = _slug(name)
        conn.execute("INSERT INTO roles (key, name, description, is_system, permissions) VALUES (?, ?, ?, 0, ?)",
                     (key, name, (description or "").strip()[:200], json.dumps(clean_permissions(permissions))))
        conn.commit()
    return get_role(key)


def update_role(key: str, name: str, description: str, permissions: List[str]) -> Dict[str, Any]:
    role = get_role(key)
    if role is None:
        raise AccessError("Perfil não encontrado.")
    if role["is_system"]:
        raise AccessError("Os perfis padrão não podem ser alterados. Crie um perfil personalizado.")
    name = (name or "").strip()
    if not 2 <= len(name) <= 40:
        raise AccessError("Dê um nome de 2 a 40 caracteres ao perfil.")
    with get_db_connection() as conn:
        if conn.execute("SELECT 1 FROM roles WHERE lower(name) = lower(?) AND key != ?", (name, key)).fetchone():
            raise AccessError("Já existe um perfil com esse nome.")
        conn.execute("UPDATE roles SET name = ?, description = ?, permissions = ?, "
                     "updated_at = datetime('now','localtime') WHERE key = ?",
                     (name, (description or "").strip()[:200], json.dumps(clean_permissions(permissions)), key))
        conn.commit()
    return get_role(key)


def delete_role(key: str) -> None:
    role = get_role(key)
    if role is None:
        raise AccessError("Perfil não encontrado.")
    if role["is_system"]:
        raise AccessError("Os perfis padrão não podem ser excluídos.")
    with get_db_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM users WHERE role = ?", (key,)).fetchone()[0]
        if n:
            raise AccessError(f"{n} usuário(s) usam este perfil. Mude o perfil deles antes de excluir.")
        conn.execute("DELETE FROM roles WHERE key = ?", (key,))
        conn.commit()


# ================================================================== usuários
_USER_COLS = ("id, username, full_name, role, active, must_change_password, created_at, "
              "last_login_at, password_hash")


def _public_user(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    d.pop("password_hash", None)
    d["active"] = bool(d.get("active"))
    d["must_change_password"] = bool(d.get("must_change_password"))
    return d


def count_users(active_admins_only: bool = False) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE COALESCE(password_hash, '') != ''"
    if active_admins_only:
        sql += f" AND active = 1 AND role = '{ROLE_ADMIN}'"
    with get_db_connection() as conn:
        return int(conn.execute(sql).fetchone()[0])


def list_users() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            f"SELECT {_USER_COLS}, (SELECT name FROM roles r WHERE r.key = u.role) AS role_name "
            "FROM users u WHERE COALESCE(password_hash, '') != '' ORDER BY active DESC, full_name COLLATE NOCASE"
        ).fetchall()
    return [_public_user(r) for r in rows]


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row)


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT {_USER_COLS} FROM users WHERE username = ?",
                           (normalize_username(username),)).fetchone()
    return _public_user(row)


def _check_role(conn, role: str) -> None:
    if not conn.execute("SELECT 1 FROM roles WHERE key = ?", (role,)).fetchone():
        raise AccessError("Perfil inexistente.")


def create_user(username: str, full_name: str, role: str, password: str,
                must_change_password: bool = True) -> Dict[str, Any]:
    username = normalize_username(username)
    if not USERNAME_RE.match(username):
        raise AccessError("Usuário: 3 a 32 caracteres, só letras minúsculas, números, ponto, hífen ou _.")
    full_name = (full_name or "").strip()
    if not full_name:
        raise AccessError("Informe o nome da pessoa.")
    problem = passwords.password_problem(password, username)
    if problem:
        raise AccessError(problem)
    with get_db_connection() as conn:
        _check_role(conn, role)
        existing = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if existing and existing["password_hash"]:
            raise AccessError("Esse nome de usuário já existe.")
        values = (full_name[:80], role, passwords.hash_password(password), int(bool(must_change_password)), _now())
        if existing:   # linha antiga da tabela users, sem senha: reaproveita
            conn.execute("UPDATE users SET full_name = ?, role = ?, password_hash = ?, must_change_password = ?, "
                         "active = 1, created_at = ? WHERE id = ?", values + (existing["id"],))
        else:
            conn.execute("INSERT INTO users (full_name, role, password_hash, must_change_password, active, "
                         "created_at, username) VALUES (?, ?, ?, ?, 1, ?, ?)", values + (username,))
        conn.commit()
    return get_user_by_username(username)


def _would_remove_last_admin(conn, user_id: int, new_role: str, new_active: bool) -> bool:
    row = conn.execute("SELECT role, active FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or row["role"] != ROLE_ADMIN or not row["active"]:
        return False
    if new_role == ROLE_ADMIN and new_active:
        return False
    others = conn.execute("SELECT COUNT(*) FROM users WHERE role = ? AND active = 1 AND id != ? "
                          "AND COALESCE(password_hash, '') != ''", (ROLE_ADMIN, user_id)).fetchone()[0]
    return others == 0


def update_user(user_id: int, full_name: str, role: str, active: bool) -> Dict[str, Any]:
    with get_db_connection() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise AccessError("Usuário não encontrado.")
        _check_role(conn, role)
        if _would_remove_last_admin(conn, user_id, role, active):
            raise AccessError("Este é o único administrador ativo: crie outro antes de mudar ou desativar este.")
        full_name = (full_name or "").strip()
        if not full_name:
            raise AccessError("Informe o nome da pessoa.")
        conn.execute("UPDATE users SET full_name = ?, role = ?, active = ?, updated_at = ? WHERE id = ?",
                     (full_name[:80], role, int(bool(active)), _now(), user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    return get_user(user_id)


def set_password(user_id: int, password: str, must_change: bool) -> None:
    user = get_user(user_id)
    if user is None:
        raise AccessError("Usuário não encontrado.")
    problem = passwords.password_problem(password, user["username"])
    if problem:
        raise AccessError(problem)
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
                     (passwords.hash_password(password), int(bool(must_change)), _now(), user_id))
        conn.commit()


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Usuário ativo com a senha certa, ou None. Mesmo custo quando o usuário não existe."""
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT {_USER_COLS} FROM users WHERE username = ?",
                           (normalize_username(username),)).fetchone()
    stored = row["password_hash"] if row else ""
    ok = passwords.verify_password(password, stored or _DUMMY_HASH)
    if not row or not stored or not ok or not row["active"]:
        return None
    return _public_user(row)


_DUMMY_HASH = passwords.hash_password(secrets.token_hex(8), iterations=passwords.ITERATIONS)


# ================================================================== sessões
def create_session(user_id: int, hours: float, ip: str = "", user_agent: str = "") -> str:
    """Devolve o token (vai só no cookie); o banco guarda apenas o hash dele."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=hours)).strftime(DT_FMT)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at, ip, user_agent) VALUES (?, ?, ?, ?, ?)",
                     (_token_hash(token), user_id, expires, ip[:64], (user_agent or "")[:200]))
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
        conn.commit()
    return token


def get_session_user(token: str, touch_after_sec: int = 60) -> Optional[Dict[str, Any]]:
    """Usuário dono de uma sessão válida (não expirada, usuário ativo) + suas permissões."""
    if not token or len(token) > 200:
        return None
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT s.id AS session_id, s.expires_at, s.last_seen_at, u.id, u.username, u.full_name, u.role, "
            "u.active, u.must_change_password FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
        if row is None:
            return None
        now = datetime.now()
        if row["expires_at"] <= now.strftime(DT_FMT) or not row["active"]:
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
            conn.commit()
            return None
        try:
            seen = datetime.strptime(row["last_seen_at"], DT_FMT)
        except (TypeError, ValueError):
            seen = None
        if seen is None or (now - seen).total_seconds() > touch_after_sec:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now.strftime(DT_FMT), row["session_id"]))
            conn.commit()
    user = dict(row)
    user["active"] = bool(user["active"])
    user["must_change_password"] = bool(user["must_change_password"])
    user["permissions"] = role_permissions(user["role"])
    role = get_role(user["role"])
    user["role_name"] = role["name"] if role else user["role"]
    return user


def delete_session(token: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token or ""),))
        conn.commit()


def delete_user_sessions(user_id: int, except_token: str = "") -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                     (user_id, _token_hash(except_token) if except_token else ""))
        conn.commit()


def purge_expired_sessions() -> int:
    with get_db_connection() as conn:
        n = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),)).rowcount
        conn.commit()
    return n


# ================================================================== auditoria
def log_audit(action: str, user: Optional[Dict[str, Any]] = None, target_type: str = "",
              target_id: Any = "", detail: str = "", ip: str = "", username: str = "") -> None:
    """Nunca derruba a operação principal: falha de auditoria só vai para o log."""
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO audit_logs (user_id, username, action, target_type, target_id, detail, ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ((user or {}).get("id"), ((user or {}).get("username") or username or "")[:64], action[:64],
                 (target_type or "")[:32], str(target_id if target_id is not None else "")[:64],
                 (detail or "")[:500], (ip or "")[:64]))
            conn.commit()
    except Exception as e:
        logger.error(f"Falha ao gravar auditoria ({action}): {e}")


def list_audit(limit: int = 100, before_id: int = 0, action: str = "", username: str = "",
               days: int = 30) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM audit_logs WHERE created_at >= datetime('now','localtime', ?)"
    params: list = [f"-{int(days)} days"]
    if before_id:
        sql += " AND id < ?"
        params.append(int(before_id))
    if action:
        sql += " AND action LIKE ?"
        params.append(action.strip() + "%")
    if username:
        sql += " AND username = ?"
        params.append(normalize_username(username))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def purge_old_audit(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    with get_db_connection() as conn:
        n = conn.execute("DELETE FROM audit_logs WHERE created_at < datetime('now','localtime', ?)",
                         (f"-{int(retention_days)} days",)).rowcount
        conn.commit()
    return n


# ---------------------------------------------------------------- conta ↔ Telegram
# Quem é da segurança liga a SUA conta do RealGuard ao Telegram uma vez
# (Minha conta → Vincular Telegram). Assim, quem toca em "Confirmar" num
# cartão do bot é identificado e precisa ter a permissão events.decide —
# exatamente como no painel — e a auditoria registra "via Telegram".
TELEGRAM_CODE_MINUTES = 15


def create_telegram_code(user_id: int) -> str:
    code = "us" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:14]
    expires = (datetime.now() + timedelta(minutes=TELEGRAM_CODE_MINUTES)).strftime(DT_FMT)
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET telegram_link_code = ?, telegram_code_expires = ? WHERE id = ?",
                     (code, expires, int(user_id)))
        conn.commit()
    return code


def link_telegram_user(code: str, telegram_user_id: str, telegram_name: str = "") -> Optional[Dict[str, Any]]:
    """Usa o código (uso único, 15 min). Um Telegram só fica ligado a uma conta."""
    code = (code or "").strip()
    if not code.startswith("us") or len(code) > 40:
        return None
    now = datetime.now().strftime(DT_FMT)
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE telegram_link_code = ? AND telegram_code_expires >= ? "
                           "AND active = 1", (code, now)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE users SET telegram_user_id = '', telegram_name = '' WHERE telegram_user_id = ?",
                     (str(telegram_user_id),))
        conn.execute("UPDATE users SET telegram_user_id = ?, telegram_name = ?, telegram_link_code = '', "
                     "telegram_code_expires = '' WHERE id = ?",
                     (str(telegram_user_id), (telegram_name or "")[:80], row["id"]))
        conn.commit()
    return user_by_telegram(telegram_user_id)


def user_by_telegram(telegram_user_id: Any) -> Optional[Dict[str, Any]]:
    """Usuário ATIVO ligado a este Telegram, com as permissões do perfil (ou None)."""
    if not telegram_user_id:
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, username, full_name, role, active, telegram_user_id, telegram_name "
                           "FROM users WHERE telegram_user_id = ? AND active = 1 "
                           "AND COALESCE(password_hash,'') != ''", (str(telegram_user_id),)).fetchone()
    if not row:
        return None
    user = dict(row)
    user["permissions"] = role_permissions(user["role"])
    return user


def telegram_status(user_id: int) -> Dict[str, Any]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT telegram_user_id, telegram_name, telegram_alerts FROM users WHERE id = ?",
                           (int(user_id),)).fetchone()
    return {"vinculado": bool(row and row["telegram_user_id"]), "nome": (row["telegram_name"] if row else "") or "",
            "alertas": bool(row and row["telegram_alerts"])}


def set_telegram_alerts(user_id: int, on: bool) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET telegram_alerts = ? WHERE id = ?", (1 if on else 0, int(user_id)))
        conn.commit()


def security_people() -> List[Dict[str, Any]]:
    """
    Todos os usuários ATIVOS que podem ver ocorrências, com a situação no Telegram.
    recebe = vinculado e com os alertas ligados (é a lista de quem recebe os cartões).
    """
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT u.id, u.username, u.full_name, u.role, u.telegram_user_id, u.telegram_name, u.telegram_alerts, "
            "(SELECT name FROM roles r WHERE r.key = u.role) AS role_name FROM users u "
            "WHERE u.active = 1 AND COALESCE(u.password_hash,'') != '' ORDER BY u.full_name COLLATE NOCASE")]
    out = []
    for r in rows:
        perms = role_permissions(r["role"])
        if "events.view" not in perms:
            continue
        linked = bool(r["telegram_user_id"])
        out.append({"id": r["id"], "usuario": r["username"], "nome": r["full_name"] or r["username"],
                    "perfil": r["role_name"] or r["role"], "chat": r["telegram_user_id"] or "",
                    "vinculado": linked, "alertas": bool(r["telegram_alerts"]),
                    "recebe": linked and bool(r["telegram_alerts"]), "decide": "events.decide" in perms})
    return out


def alert_recipients() -> List[Dict[str, Any]]:
    """Quem recebe os alertas de segurança no Telegram (privado de cada um)."""
    return [p for p in security_people() if p["recebe"]]


def unlink_telegram_user(user_id: int) -> str:
    """Desfaz o vínculo. Devolve o id do Telegram que estava ligado ('' se nenhum)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT telegram_user_id FROM users WHERE id = ?", (int(user_id),)).fetchone()
        conn.execute("UPDATE users SET telegram_user_id = '', telegram_name = '', telegram_link_code = '' "
                     "WHERE id = ?", (int(user_id),))
        conn.commit()
    return (row["telegram_user_id"] if row else "") or ""


def linked_telegram_users() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, username, full_name, role, telegram_user_id FROM users "
            "WHERE telegram_user_id != '' AND active = 1")]
