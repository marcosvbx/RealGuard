"""
Presença do dia (consolidação) e correções humanas.

As passagens da câmera (`presences`) são o fato bruto e nunca são alteradas.
A partir delas, uma linha por aluno por dia letivo vai para `attendance_days`
com a situação do dia: PRESENTE, ATRASADO, AUSENTE, JUSTIFICADA ou PENDENTE
(ainda dentro do horário). O dia de hoje é recalculado a cada consulta; os
dias anteriores são "fechados" (closed=1) e não mudam mais sozinhos — assim
a limpeza das passagens antigas (retenção) não apaga o histórico escolar.

Correção humana (marcar presença, corrigir horário, justificar falta) fica
nas colunas manual_* com quem e quando; a auditoria registra a ação.
"""

import logging
import os
from datetime import timedelta
from config.clock import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from database import school
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

PRESENTE, ATRASADO, AUSENTE, JUSTIFICADA, PENDENTE = "PRESENTE", "ATRASADO", "AUSENTE", "JUSTIFICADA", "PENDENTE"
PRESENT_SET = (PRESENTE, ATRASADO)
LABELS = {PRESENTE: "Presente", ATRASADO: "Atrasado", AUSENTE: "Ausente", JUSTIFICADA: "Falta justificada",
          PENDENTE: "Aguardando"}
MAX_BACKFILL_DAYS = 400
_COLS = ("student_id", "day", "shift_id", "class_name", "deadline", "shift_end", "cam_entry", "cam_exit",
         "last_direction", "manual_entry", "manual_status", "manual_reason", "manual_by", "manual_by_name",
         "manual_at", "status", "early_exit", "closed")


class AttendanceError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now()


def derive(row: Dict[str, Any], day: date, now: datetime) -> Tuple[str, int]:
    """Situação do dia a partir dos campos guardados (câmera + correção humana)."""
    entry = row.get("manual_entry") or row.get("cam_entry")
    if row.get("manual_status") == JUSTIFICADA:
        status = JUSTIFICADA
    elif entry:
        status = ATRASADO if school.minutes(entry) > school.minutes(row["deadline"]) else PRESENTE
    elif day < now.date() or now.hour * 60 + now.minute > school.minutes(row["deadline"]):
        status = AUSENTE
    else:
        status = PENDENTE
    early = int(bool(status in PRESENT_SET and row.get("last_direction") == "EXIT" and row.get("cam_exit")
                     and school.minutes(row["cam_exit"]) < school.minutes(row["shift_end"])))
    return status, early


def _upsert(conn, row: Dict[str, Any]) -> None:
    conn.execute(
        f"INSERT INTO attendance_days ({', '.join(_COLS)}, updated_at) VALUES ({', '.join('?' for _ in _COLS)}, "
        "rg_now()) ON CONFLICT(student_id, day) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in _COLS[2:]) + ", updated_at = excluded.updated_at",
        [row.get(c) for c in _COLS])


def consolidate_day(day: date, now: Optional[datetime] = None, cal: Optional[school.Calendar] = None,
                    force: bool = False) -> List[Dict[str, Any]]:
    """
    Calcula (e grava) a presença de todos os alunos num dia. Dias fechados só
    são recalculados com force=True (ex.: feriado cadastrado depois), sempre
    preservando as correções humanas.
    """
    now = now or _now()
    if day > now.date():
        return []
    cal = cal or school.Calendar.load()
    iso = day.isoformat()
    with get_db_connection() as conn:
        students = conn.execute("SELECT id, class_name, created_at FROM students").fetchall()
        passages: Dict[int, List[Tuple[str, str]]] = {}
        for r in conn.execute("SELECT student_id, direction, strftime('%H:%M', created_at) AS t FROM presences "
                              "WHERE student_id IS NOT NULL AND date(created_at) = ? ORDER BY id", (iso,)):
            passages.setdefault(r["student_id"], []).append((r["direction"], r["t"]))
        existing = {r["student_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM attendance_days WHERE day = ?", (iso,))}
        for s in students:
            ex = existing.get(s["id"])
            if ex and ex["closed"] and not force:
                continue
            created = (s["created_at"] or "")[:10]
            shift = cal.shift_for_class(s["class_name"])
            is_school = bool(shift) and cal.day_status(day, shift["id"])[0] and not (created and created > iso)
            if not is_school:
                if ex and not ex.get("manual_status") and not ex.get("manual_entry"):
                    conn.execute("DELETE FROM attendance_days WHERE id = ?", (ex["id"],))
                continue
            items = passages.get(s["id"], [])
            entries = [t for d, t in items if d == "ENTRY"]
            exits = [t for d, t in items if d == "EXIT"]
            row = dict(ex or {})
            keep_rules = bool(ex and ex["closed"])   # dia fechado: mantém o horário do turno daquele dia
            row.update(
                student_id=s["id"], day=iso, class_name=s["class_name"] or "",
                shift_id=row["shift_id"] if keep_rules else shift["id"],
                deadline=row["deadline"] if keep_rules else shift["deadline"],
                shift_end=row["shift_end"] if keep_rules else shift["end_time"],
                cam_entry=min(entries) if entries else None, cam_exit=max(exits) if exits else None,
                last_direction=items[-1][0] if items else None,
            )
            row["status"], row["early_exit"] = derive(row, day, now)
            row["closed"] = 1 if day < now.date() else 0
            _upsert(conn, row)
        conn.commit()
    return day_rows(day)


def day_rows(day: date, student_id: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = ("SELECT a.*, s.name, s.registration_number, s.telegram_chat_id, sh.name AS shift_name "
           "FROM attendance_days a LEFT JOIN students s ON s.id = a.student_id "
           "LEFT JOIN shifts sh ON sh.id = a.shift_id WHERE a.day = ?")
    params: list = [day.isoformat()]
    if student_id is not None:
        sql += " AND a.student_id = ?"
        params.append(int(student_id))
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY s.name COLLATE NOCASE", params)]


def _closed_until() -> Optional[date]:
    value = school.get_settings().get("attendance_closed_until", "")
    try:
        return school.parse_day(value) if value else None
    except school.SchoolError:
        return None


def _set_closed_until(day: date) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO school_settings (key, value) VALUES ('attendance_closed_until', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (day.isoformat(),))
        conn.commit()


def close_pending_days(now: Optional[datetime] = None) -> int:
    """Fecha os dias anteriores a hoje que ainda não foram consolidados. Devolve quantos dias processou."""
    now = now or _now()
    yesterday = now.date() - timedelta(days=1)
    last = _closed_until()
    if last is None:
        with get_db_connection() as conn:
            first = conn.execute("SELECT MIN(date(created_at)) FROM presences").fetchone()[0]
        start = school.parse_day(first) if first else yesterday
    else:
        start = last + timedelta(days=1)
    start = max(start, now.date() - timedelta(days=MAX_BACKFILL_DAYS))
    if start > yesterday:
        return 0
    cal = school.Calendar.load()
    d, n = start, 0
    while d <= yesterday:
        consolidate_day(d, now=now, cal=cal)
        d += timedelta(days=1)
        n += 1
    _set_closed_until(yesterday)
    return n


def recompute_range(start: date, end: date, now: Optional[datetime] = None) -> int:
    """Recalcula dias (inclusive fechados) após mudança no calendário; respeita a retenção das passagens."""
    now = now or _now()
    keep = int(school.get_settings().get("retention_passages_days") or 180)
    start = max(start, now.date() - timedelta(days=keep))
    end = min(end, now.date())
    cal = school.Calendar.load()
    d, n = start, 0
    while d <= end:
        consolidate_day(d, now=now, cal=cal, force=True)
        d += timedelta(days=1)
        n += 1
    return n


def ensure_range(start: date, end: date, now: Optional[datetime] = None) -> None:
    """Garante a consolidação do período (fecha o que falta e recalcula hoje)."""
    now = now or _now()
    close_pending_days(now)
    if start <= now.date() <= end:
        consolidate_day(now.date(), now=now)


# ------------------------------------------------------------------ correções humanas
def _row(student_id: int, day: date, now: datetime) -> Dict[str, Any]:
    if day > now.date():
        raise AttendanceError("Não dá para registrar presença em data futura.")
    rows = day_rows(day, student_id)
    if not rows:
        consolidate_day(day, now=now, force=True)
        rows = day_rows(day, student_id)
    if not rows:
        raise AttendanceError("Este dia não é letivo para o aluno (feriado, fim de semana ou fora do ano letivo).")
    return rows[0]


def _save_manual(row: Dict[str, Any], day: date, now: datetime, **fields) -> Dict[str, Any]:
    row.update(fields)
    row["status"], row["early_exit"] = derive(row, day, now)
    with get_db_connection() as conn:
        conn.execute("UPDATE attendance_days SET manual_entry = ?, manual_status = ?, manual_reason = ?, "
                     "manual_by = ?, manual_by_name = ?, manual_at = ?, status = ?, early_exit = ?, "
                     "updated_at = rg_now() WHERE id = ?",
                     (row.get("manual_entry"), row.get("manual_status"), row.get("manual_reason") or "",
                      row.get("manual_by"), row.get("manual_by_name") or "", row.get("manual_at"),
                      row["status"], row["early_exit"], row["id"]))
        conn.commit()
    return day_rows(day, row["student_id"])[0]


def mark_present(student_id: int, day: date, time_hhmm: str, user: Dict[str, Any], reason: str = "",
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    if not school.TIME_RE.match(time_hhmm or ""):
        raise AttendanceError("Horário no formato HH:MM.")
    if day == now.date() and school.minutes(time_hhmm) > now.hour * 60 + now.minute:
        raise AttendanceError("O horário de entrada não pode estar no futuro.")
    row = _row(student_id, day, now)
    return _save_manual(row, day, now, manual_entry=time_hhmm, manual_status=None,
                        manual_reason=(reason or "Presença registrada manualmente").strip()[:200],
                        manual_by=user.get("id"), manual_by_name=user.get("full_name") or user.get("username"),
                        manual_at=now.strftime("%Y-%m-%d %H:%M:%S"))


def justify(student_id: int, day: date, reason: str, user: Dict[str, Any],
            now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise AttendanceError("Informe o motivo da justificativa (ex.: atestado médico).")
    row = _row(student_id, day, now)
    return _save_manual(row, day, now, manual_status=JUSTIFICADA, manual_reason=reason[:200],
                        manual_by=user.get("id"), manual_by_name=user.get("full_name") or user.get("username"),
                        manual_at=now.strftime("%Y-%m-%d %H:%M:%S"))


def clear_manual(student_id: int, day: date, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    row = _row(student_id, day, now)
    return _save_manual(row, day, now, manual_entry=None, manual_status=None, manual_reason="",
                        manual_by=None, manual_by_name="", manual_at=None)


def student_history(student_id: int, days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    start = now.date() - timedelta(days=max(1, min(days, 400)) - 1)
    ensure_range(start, now.date(), now)
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT a.*, sh.name AS shift_name FROM attendance_days a LEFT JOIN shifts sh ON sh.id = a.shift_id "
            "WHERE a.student_id = ? AND a.day >= ? ORDER BY a.day DESC", (int(student_id), start.isoformat()))]
    counted = [r for r in rows if r["status"] != PENDENTE]
    present = sum(1 for r in counted if r["status"] in PRESENT_SET)
    return {
        "dias": rows,
        "resumo": {
            "dias_letivos": len(counted), "presencas": present,
            "atrasos": sum(1 for r in counted if r["status"] == ATRASADO),
            "faltas": sum(1 for r in counted if r["status"] == AUSENTE),
            "justificadas": sum(1 for r in counted if r["status"] == JUSTIFICADA),
            "saidas_antecipadas": sum(r["early_exit"] for r in counted),
            "frequencia": round(100 * present / len(counted), 1) if counted else None,
        },
    }


# ------------------------------------------------------------------ retenção (LGPD)
def purge_old_passages(keep_days: int, now: Optional[datetime] = None) -> int:
    """Apaga passagens brutas antigas — só de dias já consolidados (o histórico do dia fica)."""
    now = now or _now()
    closed = _closed_until()
    if closed is None or keep_days < 1:
        return 0
    cutoff = min(now.date() - timedelta(days=keep_days), closed + timedelta(days=1))
    with get_db_connection() as conn:
        n = conn.execute("DELETE FROM presences WHERE date(created_at) < ?", (cutoff.isoformat(),)).rowcount
        conn.commit()
    return n


def purge_old_event_photos(keep_days: int, now: Optional[datetime] = None) -> int:
    """Apaga as fotos de alertas mais antigas que keep_days (o registro do alerta continua)."""
    from database.repository import event_snapshot_path
    now = now or _now()
    if keep_days < 1:
        return 0
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM security_events WHERE created_at < ?", (cutoff,))]
    removed = 0
    for event_id in ids:
        path = event_snapshot_path(event_id)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                logger.warning(f"Não consegui apagar a foto do alerta {event_id}: {e}")
    return removed
