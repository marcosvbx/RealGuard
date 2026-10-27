"""
Retirada autorizada: quem pode buscar o aluno num dia específico.

Quem autoriza é sempre o RESPONSÁVEL (pelo bot do Telegram), nunca a escola:
* pedido feito pelo responsável principal  -> vale na hora (ACTIVE);
* pedido de outro responsável              -> PENDING até o principal aprovar
  (cartão com foto e [Aprovar] [Recusar]); sem resposta, expira no fim do dia.

A escola só VÊ (ficha do aluno, para a portaria conferir) e pode cancelar se o
responsável ligar pedindo. A foto de quem busca é opcional, fica em
data/retiradas/<id>.jpg (fora do banco) e é apagada 7 dias depois da data —
o registro (nome, parentesco, quem autorizou) continua no histórico.
"""

import json
import logging
import os
from datetime import timedelta
from config.clock import date, datetime
from typing import Any, Dict, List, Optional

from config.settings import DEFAULT_DB_PATH
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

PENDING, ACTIVE, REJECTED, CANCELLED, EXPIRED = "PENDING", "ACTIVE", "REJECTED", "CANCELLED", "EXPIRED"
LABELS = {PENDING: "Aguardando o responsável principal", ACTIVE: "Autorizada", REJECTED: "Recusada",
          CANCELLED: "Cancelada", EXPIRED: "Expirou sem resposta"}
MAX_DAYS_AHEAD = 30
MAX_PER_DAY = 5
PHOTO_KEEP_DAYS = 7


class PickupError(ValueError):
    pass


def photos_dir() -> str:
    base = os.path.dirname(os.path.abspath(os.getenv("DB_PATH", DEFAULT_DB_PATH)))
    return os.path.join(base, "retiradas")


def photo_path(pickup_id: int) -> str:
    return os.path.join(photos_dir(), f"{int(pickup_id)}.jpg")


def has_photo(pickup_id: int) -> bool:
    return os.path.isfile(photo_path(pickup_id))


def save_photo(pickup_id: int, jpeg: bytes) -> None:
    os.makedirs(photos_dir(), exist_ok=True)
    path = photo_path(pickup_id)
    with open(path, "wb") as f:
        f.write(jpeg)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    with get_db_connection() as conn:
        conn.execute("UPDATE pickup_authorizations SET has_photo = 1 WHERE id = ?", (int(pickup_id),))
        conn.commit()


def _today() -> date:
    return date.today()


def effective_status(row: Dict[str, Any], today: Optional[date] = None) -> str:
    """PENDING de um dia que já passou = EXPIRED (calculado, nada a gravar)."""
    today = today or _today()
    if row.get("status") == PENDING and str(row.get("day") or "") < today.isoformat():
        return EXPIRED
    return row.get("status") or PENDING


def _public(r) -> Dict[str, Any]:
    d = dict(r)
    d["situacao"] = effective_status(d)
    d["situacao_texto"] = LABELS.get(d["situacao"], d["situacao"])
    d["tem_foto"] = bool(d.get("has_photo")) and has_photo(d["id"])
    return d


_SELECT = ("SELECT p.*, s.name AS student_name, s.class_name, rq.name AS requested_by_name, "
           "dc.name AS decided_by_name, "
           "(SELECT relationship FROM student_guardians x WHERE x.student_id = p.student_id "
           " AND x.guardian_id = p.requested_by) AS requested_by_relationship "
           "FROM pickup_authorizations p JOIN students s ON s.id = p.student_id "
           "LEFT JOIN guardians rq ON rq.id = p.requested_by LEFT JOIN guardians dc ON dc.id = p.decided_by ")


def get(pickup_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute(_SELECT + "WHERE p.id = ?", (int(pickup_id),)).fetchone()
    return _public(r) if r else None


def for_student(student_id: int, past_days: int = 30) -> List[Dict[str, Any]]:
    """Próximas primeiro (por data), depois o histórico recente."""
    since = (_today() - timedelta(days=past_days)).isoformat()
    with get_db_connection() as conn:
        rows = [_public(r) for r in conn.execute(
            _SELECT + "WHERE p.student_id = ? AND p.day >= ? ORDER BY p.day DESC, p.id DESC",
            (int(student_id), since))]
    today = _today().isoformat()
    upcoming = sorted([r for r in rows if r["day"] >= today], key=lambda r: (r["day"], r["id"]))
    return upcoming + [r for r in rows if r["day"] < today]


def upcoming_by_student(student_ids: Optional[List[int]] = None) -> Dict[int, List[Dict[str, Any]]]:
    """Para a lista de alunos: autorizações de hoje em diante que ainda valem ou aguardam o principal."""
    today = _today().isoformat()
    with get_db_connection() as conn:
        rows = [_public(r) for r in conn.execute(
            _SELECT + "WHERE p.day >= ? AND p.status IN ('PENDING','ACTIVE') ORDER BY p.day, p.id", (today,))]
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        if student_ids is None or r["student_id"] in student_ids:
            out.setdefault(r["student_id"], []).append(r)
    return out


def for_day(day: Optional[date] = None) -> List[Dict[str, Any]]:
    """Todas as retiradas do dia (painel da portaria / Notificações), na ordem em que foram pedidas."""
    day = (day or _today()).isoformat()
    with get_db_connection() as conn:
        return [_public(r) for r in conn.execute(_SELECT + "WHERE p.day = ? ORDER BY p.id", (day,))]


def active_for_day(student_id: int, day: Optional[date] = None) -> List[Dict[str, Any]]:
    day = (day or _today()).isoformat()
    with get_db_connection() as conn:
        return [_public(r) for r in conn.execute(
            _SELECT + "WHERE p.student_id = ? AND p.day = ? AND p.status = 'ACTIVE' ORDER BY p.id",
            (int(student_id), day))]


def for_chat(chat_id: str, student_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Autorizações de hoje em diante dos alunos deste Telegram."""
    today = _today().isoformat()
    sql = (_SELECT + "WHERE p.day >= ? AND p.status IN ('PENDING','ACTIVE') AND p.student_id IN "
           "(SELECT sg.student_id FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id "
           " WHERE g.telegram_chat_id = ?)")
    params: list = [today, str(chat_id)]
    if student_id is not None:
        sql += " AND p.student_id = ?"
        params.append(int(student_id))
    with get_db_connection() as conn:
        return [_public(r) for r in conn.execute(sql + " ORDER BY p.day, p.id", params)]


def _link(conn, chat_id: str, student_id: int) -> Optional[Dict[str, Any]]:
    r = conn.execute("SELECT g.id AS guardian_id, g.name, sg.is_primary FROM guardians g JOIN student_guardians sg "
                     "ON sg.guardian_id = g.id WHERE g.telegram_chat_id = ? AND sg.student_id = ? "
                     "ORDER BY sg.is_primary DESC LIMIT 1", (str(chat_id), int(student_id))).fetchone()
    return dict(r) if r else None


def create(chat_id: str, student_id: int, person_name: str, relationship: str, day: date) -> Dict[str, Any]:
    """Pedido feito pelo Telegram. O vínculo responsável↔aluno é conferido aqui, no banco."""
    name = " ".join((person_name or "").split())
    rel = " ".join((relationship or "").split())
    if not 3 <= len(name) <= 80:
        raise PickupError("O nome de quem vai buscar precisa ter de 3 a 80 letras.")
    if not 2 <= len(rel) <= 40:
        raise PickupError("Informe o parentesco ou a relação com o aluno.")
    today = _today()
    if day < today:
        raise PickupError("Escolha hoje ou uma data futura.")
    if day > today + timedelta(days=MAX_DAYS_AHEAD):
        raise PickupError(f"Dá para autorizar com até {MAX_DAYS_AHEAD} dias de antecedência.")
    with get_db_connection() as conn:
        link = _link(conn, chat_id, student_id)
        if link is None:
            raise PickupError("Este aluno não está mais ligado ao seu Telegram. Fale com a escola.")
        n = conn.execute("SELECT COUNT(*) FROM pickup_authorizations WHERE student_id = ? AND day = ? "
                         "AND status IN ('PENDING','ACTIVE')", (int(student_id), day.isoformat())).fetchone()[0]
        if n >= MAX_PER_DAY:
            raise PickupError(f"Já há {MAX_PER_DAY} pessoas autorizadas para este dia.")
        primary = bool(link["is_primary"])
        cur = conn.execute(
            "INSERT INTO pickup_authorizations (student_id, day, person_name, relationship, status, requested_by, "
            "requested_chat, decided_by, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(student_id), day.isoformat(), name, rel, ACTIVE if primary else PENDING, link["guardian_id"],
             str(chat_id), link["guardian_id"] if primary else None,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S") if primary else None))
        conn.commit()
        pid = cur.lastrowid
    return get(pid)


def decide(pickup_id: int, chat_id: str, approve: bool) -> Dict[str, Any]:
    """Aprovar/recusar: só o responsável PRINCIPAL do aluno, só enquanto pendente e dentro do dia."""
    p = get(pickup_id)
    if p is None:
        raise PickupError("Pedido não encontrado.")
    with get_db_connection() as conn:
        link = _link(conn, chat_id, p["student_id"])
        if link is None or not link["is_primary"]:
            raise PickupError("Só o responsável principal pode aprovar ou recusar.")
        if p["situacao"] != PENDING:
            raise PickupError("Este pedido já foi " + LABELS.get(p["situacao"], "resolvido").lower() + ".")
        cur = conn.execute("UPDATE pickup_authorizations SET status = ?, decided_by = ?, "
                           "decided_at = datetime('now','localtime') WHERE id = ? AND status = 'PENDING'",
                           (ACTIVE if approve else REJECTED, link["guardian_id"], int(pickup_id)))
        conn.commit()
        if not cur.rowcount:
            raise PickupError("Este pedido acabou de ser resolvido por outra pessoa.")
    return get(pickup_id)


def cancel(pickup_id: int, by: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Cancelar: pelo Telegram, quem pediu ou o principal; pela escola (chat_id=None), quem tem
    students.edit (a rota confere). Só antes/no dia.
    """
    p = get(pickup_id)
    if p is None:
        raise PickupError("Autorização não encontrada.")
    if p["situacao"] not in (PENDING, ACTIVE) or p["day"] < _today().isoformat():
        raise PickupError("Esta autorização não pode mais ser cancelada.")
    with get_db_connection() as conn:
        if chat_id is not None:
            link = _link(conn, chat_id, p["student_id"])
            if link is None or not (link["is_primary"] or str(p["requested_chat"]) == str(chat_id)):
                raise PickupError("Só quem pediu ou o responsável principal pode cancelar.")
        conn.execute("UPDATE pickup_authorizations SET status = 'CANCELLED', cancelled_by = ? WHERE id = ? "
                     "AND status IN ('PENDING','ACTIVE')", ((by or "")[:80], int(pickup_id)))
        conn.commit()
    return get(pickup_id)


def set_approval_msgs(pickup_id: int, msgs: Dict[str, int]) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE pickup_authorizations SET approval_msgs = ? WHERE id = ?",
                     (json.dumps(msgs) if msgs else "", int(pickup_id)))
        conn.commit()


def approval_msgs(p: Dict[str, Any]) -> Dict[str, int]:
    try:
        d = json.loads(p.get("approval_msgs") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def primary_chats(student_id: int) -> List[str]:
    with get_db_connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT g.telegram_chat_id FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id "
            "WHERE sg.student_id = ? AND sg.is_primary = 1 AND g.telegram_chat_id != ''", (int(student_id),))]


def purge_photos(keep_days: int = PHOTO_KEEP_DAYS, today: Optional[date] = None) -> int:
    """Retenção (LGPD): apaga a foto de quem buscou N dias depois da data. O registro continua."""
    cutoff = ((today or _today()) - timedelta(days=keep_days)).isoformat()
    with get_db_connection() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM pickup_authorizations WHERE has_photo = 1 AND day < ?",
                                          (cutoff,))]
        removed = 0
        for pid in ids:
            try:
                os.remove(photo_path(pid))
                removed += 1
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"Foto da retirada {pid} não apagada: {e}")
                continue
            conn.execute("UPDATE pickup_authorizations SET has_photo = 0 WHERE id = ?", (pid,))
        conn.commit()
    return removed


def remove_student(conn, student_id: int) -> None:
    ids = [r[0] for r in conn.execute("SELECT id FROM pickup_authorizations WHERE student_id = ?", (int(student_id),))]
    for pid in ids:
        try:
            os.remove(photo_path(pid))
        except OSError:
            pass
    conn.execute("DELETE FROM pickup_authorizations WHERE student_id = ?", (int(student_id),))
