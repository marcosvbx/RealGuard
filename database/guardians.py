"""
Responsáveis dos alunos (vários por aluno; um responsável pode ter vários alunos).

Cada responsável tem nome, celular, foto (opcional) e o PRÓPRIO Telegram;
o parentesco fica na ligação com cada aluno (a mesma pessoa pode ser "mãe"
de um e "tia" de outro). Todos os responsáveis com Telegram vinculado
recebem os avisos do aluno.

Compatibilidade: os campos antigos do aluno (guardian_name, guardian_phone,
telegram_chat_id) continuam preenchidos como um "espelho" do responsável
principal — o que ainda lê esses campos segue funcionando.
"""

import logging
import os
import re
import secrets
from typing import Any, Dict, Iterable, List, Optional

from config.settings import DEFAULT_DB_PATH
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

RELATIONSHIPS = ["Mãe", "Pai", "Avó", "Avô", "Tia", "Tio", "Irmã", "Irmão", "Madrasta", "Padrasto",
                 "Responsável legal", "Responsável", "Outro"]
PHOTO_MAX_SIDE = 480


class GuardianError(ValueError):
    pass


# ------------------------------------------------------------------ util
def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) > 11 and digits.startswith("55"):
        digits = digits[2:]
    return digits.lstrip("0")


def phones_match(a: str, b: str) -> bool:
    x, y = normalize_phone(a), normalize_phone(b)
    if len(x) < 10 or len(y) < 10:
        return False
    if x == y:
        return True
    return x[:2] == y[:2] and x[-8:] == y[-8:]


def clean_phone(value: str) -> str:
    value = (value or "").strip()
    digits = re.sub(r"\D", "", value)
    if value and not 10 <= len(digits) <= 13:
        raise GuardianError("Celular inválido: use DDD + número, ex.: (11) 98765-4321.")
    return value


def clean_relationship(value: str) -> str:
    value = (value or "").strip()[:40]
    return value or "Responsável"


def photos_dir() -> str:
    base = os.path.dirname(os.path.abspath(os.getenv("DB_PATH", DEFAULT_DB_PATH)))
    return os.path.join(base, "guardians")


def photo_path(guardian_id: int) -> str:
    return os.path.join(photos_dir(), f"{int(guardian_id)}.jpg")


def has_photo(guardian_id: int) -> bool:
    return os.path.isfile(photo_path(guardian_id))


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    d["telegram_vinculado"] = bool(d.get("telegram_chat_id"))
    d["tem_foto"] = has_photo(d["id"])
    return d


# ------------------------------------------------------------------ leitura
def get(guardian_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM guardians WHERE id = ?", (int(guardian_id),)).fetchone()
    return _row(r) if r else None


def for_student(student_id: int) -> List[Dict[str, Any]]:
    return for_students([student_id]).get(int(student_id), [])


def for_students(student_ids: Optional[Iterable[int]] = None) -> Dict[int, List[Dict[str, Any]]]:
    """{student_id: [responsáveis em ordem: principal primeiro]} — uma consulta só."""
    sql = ("SELECT sg.student_id, sg.relationship, sg.is_primary, g.*, "
           "(SELECT COUNT(*) FROM student_guardians x WHERE x.guardian_id = g.id) AS alunos "
           "FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id")
    params: list = []
    if student_ids is not None:
        ids = [int(i) for i in student_ids]
        if not ids:
            return {}
        sql += f" WHERE sg.student_id IN ({','.join('?' for _ in ids)})"
        params = ids
    sql += " ORDER BY sg.student_id, sg.is_primary DESC, sg.created_at, g.id"
    out: Dict[int, List[Dict[str, Any]]] = {}
    with get_db_connection() as conn:
        for r in conn.execute(sql, params):
            d = _row(r)
            out.setdefault(d.pop("student_id"), []).append(d)
    return out


def students_of(guardian_id: int) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT s.id, s.name, s.class_name, sg.relationship FROM student_guardians sg "
            "JOIN students s ON s.id = sg.student_id WHERE sg.guardian_id = ? ORDER BY s.name", (int(guardian_id),))]


def chat_ids_for_student(student_id: int) -> List[str]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT DISTINCT g.telegram_chat_id FROM student_guardians sg JOIN guardians g "
                            "ON g.id = sg.guardian_id WHERE sg.student_id = ? AND g.telegram_chat_id != '' "
                            "ORDER BY sg.is_primary DESC", (int(student_id),)).fetchall()
    return [r[0] for r in rows]


NOTICE_COLUMNS = {"entrada": "notify_entry", "saida": "notify_exit", "ausencia": "notify_absence"}


def chats_for_notice(student_id: int, kind: str) -> List[str]:
    """Chats dos responsáveis que QUEREM este tipo de aviso (entrada, saida, ausencia) — ver 🔔 no bot."""
    col = NOTICE_COLUMNS.get(kind)
    if col is None:
        return chat_ids_for_student(student_id)
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT DISTINCT g.telegram_chat_id FROM student_guardians sg JOIN guardians g "
                            f"ON g.id = sg.guardian_id WHERE sg.student_id = ? AND g.telegram_chat_id != '' "
                            f"AND g.{col} = 1 ORDER BY sg.is_primary DESC", (int(student_id),)).fetchall()
    return [r[0] for r in rows]


def links_for_chat(chat_id: str) -> List[Dict[str, Any]]:
    """
    Quem é este Telegram: um item por (responsável, aluno) — um pai com dois filhos
    tem dois itens. is_primary diz se é o responsável principal DAQUELE aluno.
    """
    if not str(chat_id or "").strip():
        return []
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT g.id AS guardian_id, g.name AS guardian_name, s.id AS student_id, s.name AS student_name, "
            "s.class_name, sg.relationship, sg.is_primary FROM guardians g "
            "JOIN student_guardians sg ON sg.guardian_id = g.id JOIN students s ON s.id = sg.student_id "
            "WHERE g.telegram_chat_id = ? ORDER BY s.name, sg.is_primary DESC", (str(chat_id),))]


def notice_prefs(chat_id: str) -> Dict[str, bool]:
    """Preferências deste Telegram (se a mesma pessoa tem dois cadastros, vale o mais restritivo)."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT notify_entry, notify_exit, notify_absence FROM guardians "
                            "WHERE telegram_chat_id = ?", (str(chat_id),)).fetchall()
    if not rows:
        return {k: True for k in NOTICE_COLUMNS}
    return {k: all(bool(r[i]) for r in rows) for i, k in enumerate(NOTICE_COLUMNS)}


def set_notice_pref(chat_id: str, kind: str, on: bool) -> None:
    col = NOTICE_COLUMNS[kind]
    with get_db_connection() as conn:
        conn.execute(f"UPDATE guardians SET {col} = ? WHERE telegram_chat_id = ?", (1 if on else 0, str(chat_id)))
        conn.commit()


def find_by_phone(phone: str, exclude_student: Optional[int] = None) -> List[Dict[str, Any]]:
    """Responsáveis já cadastrados com esse celular (para reaproveitar: irmãos)."""
    if len(normalize_phone(phone)) < 10:
        return []
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM guardians WHERE phone != ''").fetchall()
    found = [_row(r) for r in rows if phones_match(r["phone"], phone)]
    for g in found:
        g["alunos"] = students_of(g["id"])
    if exclude_student is not None:
        found = [g for g in found if all(s["id"] != int(exclude_student) for s in g["alunos"])]
    return found


def count_linked() -> int:
    with get_db_connection() as conn:
        # responsáveis fictícios ("fic-…") têm Telegram simulado: não contam como vinculados de verdade
        return conn.execute("SELECT COUNT(*) FROM guardians WHERE telegram_chat_id != '' "
                            "AND telegram_chat_id NOT LIKE 'fic-%'").fetchone()[0]


# ------------------------------------------------------------------ escrita
def sync_student(conn, student_id: int) -> None:
    """Espelho do responsável principal nos campos antigos do aluno."""
    rows = conn.execute("SELECT g.name, g.phone, g.telegram_chat_id, sg.is_primary FROM student_guardians sg "
                        "JOIN guardians g ON g.id = sg.guardian_id WHERE sg.student_id = ? "
                        "ORDER BY sg.is_primary DESC, sg.created_at, g.id", (int(student_id),)).fetchall()
    first = rows[0] if rows else None
    chat = next((r[2] for r in rows if r[2]), "")
    conn.execute("UPDATE students SET guardian_name = ?, guardian_phone = ?, telegram_chat_id = ? WHERE id = ?",
                 (first[0] if first else "", first[1] if first else "", chat, int(student_id)))


def _sync_students_of(conn, guardian_id: int) -> None:
    for (sid,) in conn.execute("SELECT student_id FROM student_guardians WHERE guardian_id = ?",
                               (int(guardian_id),)).fetchall():
        sync_student(conn, sid)


def _ensure_primary(conn, student_id: int) -> None:
    if not conn.execute("SELECT 1 FROM student_guardians WHERE student_id = ? AND is_primary = 1",
                        (int(student_id),)).fetchone():
        conn.execute("UPDATE student_guardians SET is_primary = 1 WHERE rowid = (SELECT rowid FROM student_guardians "
                     "WHERE student_id = ? ORDER BY created_at, guardian_id LIMIT 1)", (int(student_id),))


def create(name: str, phone: str = "", chat_id: str = "", ficticio: bool = False) -> int:
    name = (name or "").strip()[:80]
    phone = clean_phone(phone)
    if not name and not phone:
        raise GuardianError("Informe ao menos o nome ou o celular do responsável.")
    with get_db_connection() as conn:
        cur = conn.execute("INSERT INTO guardians (name, phone, telegram_chat_id, ficticio) VALUES (?, ?, ?, ?)",
                           (name, phone, (chat_id or "").strip(), 1 if ficticio else 0))
        conn.commit()
        return int(cur.lastrowid)


def update(guardian_id: int, name: str, phone: str) -> None:
    name = (name or "").strip()[:80]
    phone = clean_phone(phone)
    if not name and not phone:
        raise GuardianError("Informe ao menos o nome ou o celular do responsável.")
    with get_db_connection() as conn:
        conn.execute("UPDATE guardians SET name = ?, phone = ? WHERE id = ?", (name, phone, int(guardian_id)))
        _sync_students_of(conn, guardian_id)
        conn.commit()


def link(student_id: int, guardian_id: int, relationship: str = "", primary: bool = False) -> None:
    with get_db_connection() as conn:
        if primary:
            conn.execute("UPDATE student_guardians SET is_primary = 0 WHERE student_id = ?", (int(student_id),))
        conn.execute("INSERT INTO student_guardians (student_id, guardian_id, relationship, is_primary) "
                     "VALUES (?, ?, ?, ?) ON CONFLICT(student_id, guardian_id) DO UPDATE SET "
                     "relationship = excluded.relationship, is_primary = CASE WHEN excluded.is_primary = 1 "
                     "THEN 1 ELSE student_guardians.is_primary END",
                     (int(student_id), int(guardian_id), clean_relationship(relationship), 1 if primary else 0))
        _ensure_primary(conn, student_id)
        sync_student(conn, student_id)
        conn.commit()


def set_link(student_id: int, guardian_id: int, relationship: str, primary: bool) -> None:
    with get_db_connection() as conn:
        if primary:
            conn.execute("UPDATE student_guardians SET is_primary = 0 WHERE student_id = ?", (int(student_id),))
        cur = conn.execute("UPDATE student_guardians SET relationship = ?, is_primary = ? "
                           "WHERE student_id = ? AND guardian_id = ?",
                           (clean_relationship(relationship), 1 if primary else 0, int(student_id), int(guardian_id)))
        if not cur.rowcount:
            raise GuardianError("Este responsável não está ligado a este aluno.")
        _ensure_primary(conn, student_id)
        sync_student(conn, student_id)
        conn.commit()


def _delete_orphans(conn, guardian_ids: Iterable[int]) -> int:
    n = 0
    for gid in set(int(g) for g in guardian_ids):
        if conn.execute("SELECT 1 FROM student_guardians WHERE guardian_id = ?", (gid,)).fetchone():
            continue
        conn.execute("DELETE FROM guardians WHERE id = ?", (gid,))
        try:
            os.remove(photo_path(gid))
        except OSError:
            pass
        n += 1
    return n


def unlink(student_id: int, guardian_id: int) -> bool:
    """Tira o responsável do aluno; se ele não tiver mais nenhum aluno, é apagado (com a foto)."""
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM student_guardians WHERE student_id = ? AND guardian_id = ?",
                           (int(student_id), int(guardian_id)))
        _delete_orphans(conn, [guardian_id])
        _ensure_primary(conn, student_id)
        sync_student(conn, student_id)
        conn.commit()
        return cur.rowcount > 0


def remove_student(conn, student_id: int) -> None:
    """Chamado ao excluir um aluno (mesma conexão/transação)."""
    gids = [r[0] for r in conn.execute("SELECT guardian_id FROM student_guardians WHERE student_id = ?",
                                       (int(student_id),))]
    conn.execute("DELETE FROM student_guardians WHERE student_id = ?", (int(student_id),))
    _delete_orphans(conn, gids)


def save_photo(guardian_id: int, jpeg: bytes) -> None:
    os.makedirs(photos_dir(), exist_ok=True)
    path = photo_path(guardian_id)
    with open(path, "wb") as f:
        f.write(jpeg)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def delete_photo(guardian_id: int) -> bool:
    try:
        os.remove(photo_path(guardian_id))
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ Telegram
def ensure_link_token(guardian_id: int) -> Optional[str]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT telegram_link_token FROM guardians WHERE id = ?", (int(guardian_id),)).fetchone()
        if row is None:
            return None
        if row[0]:
            return row[0]
        token = "rg" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
        conn.execute("UPDATE guardians SET telegram_link_token = ? WHERE id = ?", (token, int(guardian_id)))
        conn.commit()
        return token


def link_by_token(token: str, chat_id: str) -> Optional[Dict[str, Any]]:
    """Convite aberto: grava o chat no responsável do código. Devolve o responsável + alunos."""
    token = (token or "").strip()
    if not token:
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM guardians WHERE telegram_link_token = ?", (token,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE guardians SET telegram_chat_id = ? WHERE id = ?", (str(chat_id), row[0]))
        _sync_students_of(conn, row[0])
        conn.commit()
    g = get(row[0])
    g["alunos"] = students_of(row[0])
    return g


def link_by_phone(phone: str, chat_id: str) -> List[Dict[str, Any]]:
    """Número compartilhado no bot: vincula TODOS os responsáveis com esse celular. Devolve os alunos."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id, phone FROM guardians WHERE phone != ''").fetchall()
        matched = [r[0] for r in rows if phones_match(r[1], phone)]
        for gid in matched:
            conn.execute("UPDATE guardians SET telegram_chat_id = ? WHERE id = ?", (str(chat_id), gid))
            _sync_students_of(conn, gid)
        conn.commit()
    students: Dict[int, Dict[str, Any]] = {}
    for gid in matched:
        for s in students_of(gid):
            students[s["id"]] = s
    return sorted(students.values(), key=lambda s: s["name"])


def unlink_telegram(guardian_id: int) -> bool:
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE guardians SET telegram_chat_id = '' WHERE id = ?", (int(guardian_id),))
        _sync_students_of(conn, guardian_id)
        conn.commit()
        return cur.rowcount > 0
