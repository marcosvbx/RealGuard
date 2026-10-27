"""
Aviso AUTOMÁTICO de ausência aos responsáveis.

Passado o horário-limite do turno (tolerância) + N minutos (Escola → Regras,
padrão 60; 0 = só pela portaria), cada aluno ainda AUSENTE gera um aviso no
Telegram dos responsáveis que não desligaram "ausência" no bot.

* Um aviso por aluno por dia: se já existe QUALQUER aviso de ausência hoje
  (manual ou automático, inclusive com falha), não manda de novo. Falhas
  aparecem em Ocorrências → Precisa de atenção, com reenvio manual.
* Só em dia letivo (a frequência do dia já considera calendário e turno).
* Responsáveis fictícios ("fic-…") passam pelo envio simulado, sem rede.
"""

import logging
import threading
from config.clock import datetime
from typing import Any, Dict, List, Optional

from app.notifications.messages import absence_message
from app.notifications.telegram import is_simulated
from database import attendance, notifications as nlog, school
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

SETTING = "absence_notice_after_minutes"
DEFAULT_MINUTES = 60
EVERY_SEC = 60


def minutes_after() -> int:
    try:
        return max(0, int(school.get_settings().get(SETTING, DEFAULT_MINUTES)))
    except (TypeError, ValueError):
        return DEFAULT_MINUTES


def _hm(value: str) -> int:
    h, m = (value or "00:00").split(":")[:2]
    return int(h) * 60 + int(m)


def _already_noticed(day_iso: str) -> set:
    with get_db_connection() as conn:
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT student_id FROM notification_log WHERE kind = 'ABSENCE' AND date(created_at) = ? "
            "AND student_id IS NOT NULL", (day_iso,))}


def _student(sid: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT id, name, class_name, registration_number, telegram_chat_id FROM students "
                         "WHERE id = ?", (int(sid),)).fetchone()
    return dict(r) if r else None


def _chats(student: Dict[str, Any]) -> List[str]:
    from database import guardians as gdb
    linked = gdb.chat_ids_for_student(student["id"])
    if linked:
        return gdb.chats_for_notice(student["id"], "ausencia")     # [] = todos desligaram o aviso
    legacy = (student.get("telegram_chat_id") or "").strip()
    return [legacy] if legacy else []


def due(now: Optional[datetime] = None, minutes: Optional[int] = None) -> List[Dict[str, Any]]:
    """Ausentes de hoje cujo prazo (tolerância + minutos) já passou e que ainda não foram avisados."""
    now = now or datetime.now()
    minutes = minutes_after() if minutes is None else minutes
    if minutes <= 0:
        return []
    rows = attendance.consolidate_day(now.date(), now=now)
    done = _already_noticed(now.date().isoformat())
    now_min = now.hour * 60 + now.minute
    return [r for r in rows if r["status"] == attendance.AUSENTE and r["student_id"] not in done
            and r.get("deadline") and _hm(r["deadline"]) + minutes <= now_min]


def run_once(engine, now: Optional[datetime] = None) -> Dict[str, int]:
    """Manda os avisos vencidos. Devolve {'avisados', 'sem_telegram'}."""
    out = {"avisados": 0, "sem_telegram": 0}
    now = now or datetime.now()
    if engine is None or not engine.guardians_enabled:
        return out
    real_ok = engine.notifier.enabled
    for r in due(now):
        student = _student(r["student_id"])
        if not student:
            continue
        chats = [c for c in _chats(student) if real_ok or is_simulated(c)]
        if not chats:
            out["sem_telegram"] += 1
            continue
        text = absence_message(student, r["deadline"])
        for c in chats:
            ok, info = engine.notifier.send_message(c, text, "HTML")
            nlog.log(student["id"], student.get("name", ""), "ABSENCE", chat_id=c,
                     status=nlog.SENT if ok else nlog.FAILED, detail="" if ok else info, sent_by="", at=now)
        out["avisados"] += 1
    if out["avisados"]:
        logger.info(f"Ausência: {out['avisados']} responsável(is) avisado(s) automaticamente.")
    return out


class AbsenceNoticeLoop:
    def __init__(self, engine=None):
        self.engine = engine
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="aviso-ausencia")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(20):
            return
        while not self._stop.is_set():
            try:
                run_once(self.engine)
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Aviso automático de ausência falhou: {e}")
            self._stop.wait(EVERY_SEC)
