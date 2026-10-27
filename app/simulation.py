"""
Simulação ao vivo dos alunos FICTÍCIOS (para demonstração: feira, banca, apresentação).

Os dados fictícios são gerados uma vez (até o momento da geração). Sem isto, nos
dias seguintes os 120 alunos fictícios "somem": ninguém chega, a frequência cai e
o painel parece parado. Esta thread continua a rotina deles, no relógio real:

* a cada 30 s, cria as passagens (entrada, intervalo, saída) que já "aconteceram"
  desde a última rodada — mesmo padrão dos dados gerados (turno, perfil de
  pontualidade, faltas, saídas antecipadas, dias letivos do calendário);
* se o sistema ficou desligado (ex.: gerou ontem, liga hoje na feira), recupera
  os dias que faltaram (até 21 dias), então relatórios e frequência ficam contínuos;
* de vez em quando (só ao vivo), um alerta fictício de rosto obstruído, para o sino;
* avisos aos responsáveis fictícios entram no histórico de Notificações como
  "enviados" — nada sai para o Telegram (chats "fic-…").

Os dados REAIS (alunos cadastrados com rosto, câmera de verdade) não são tocados:
a câmera registra normalmente ao mesmo tempo. Liga/desliga em Administração →
Dados fictícios. Determinístico por (dia, aluno): rodar de novo não duplica.
"""

import json
import logging
import random
import threading
from datetime import time as dtime, timedelta
from config.clock import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

STATE_KEY = "simulacao_ao_vivo"
FMT = "%Y-%m-%d %H:%M:%S"
MAX_CATCHUP_DAYS = 21
EVERY_SEC = 30
CLOCK_BACK_MIN = 2             # "última rodada" mais que isso à frente do relógio = relógio/fuso mudou
ALERT_EVERY_MIN = 35          # em média, um alerta fictício a cada ~35 min (só ao vivo e no horário de aula)


def get_state() -> Dict[str, Any]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT value FROM school_settings WHERE key = ?", (STATE_KEY,)).fetchone()
    try:
        st = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        st = {}
    st.setdefault("ligada", True)
    return st


def set_state(**values) -> Dict[str, Any]:
    st = get_state()
    st.update(values)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO school_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (STATE_KEY, json.dumps(st)))
        conn.commit()
    return st


def _persona(student_id: int) -> str:
    from database.dados_ficticios import PERSONA_WEIGHTS
    bag = [p for p, w in PERSONA_WEIGHTS for _ in range(w)]
    return random.Random(f"persona-{student_id}").choice(bag)


def plan_day(student: Dict[str, Any], day: date, cal) -> List[Tuple[datetime, str]]:
    """Passagens do aluno fictício no dia (determinístico): [(quando, ENTRY|EXIT), ...]."""
    from database import school
    from database.dados_ficticios import PERSONAS, WORST_CLASS
    shift = cal.shift_for_class(student.get("class_name") or "")
    if not shift or not cal.day_status(day, shift["id"])[0]:
        return []
    if str(student.get("created_at") or "")[:10] > day.isoformat():
        return []
    rng = random.Random(f"sim-{day.isoformat()}-{student['id']}")
    p_abs, mu, sd, p_early, p_break = PERSONAS[_persona(student["id"])]
    if day.weekday() in (0, 4):
        p_abs *= 1.6
    if rng.random() < p_abs:
        return []
    start, end = school.minutes(shift["start_time"]), school.minutes(shift["end_time"])
    delay = rng.gauss(mu, sd) + (9 if student.get("class_name") == WORST_CLASS else 0)
    arrive = max(start - 35, start + delay)

    def at(minutes: float) -> datetime:
        m = max(0, min(int(round(minutes)), 23 * 60 + 58))
        return datetime.combine(day, dtime(m // 60, m % 60, rng.randint(0, 59)))
    out = [(at(arrive), "ENTRY")]
    # pausas no meio do turno (o painel não fica parado durante a apresentação)
    breaks = 1 if rng.random() < max(p_break, 0.12) else 0
    for _ in range(breaks):
        mid = rng.uniform(start + 40, end - 40)
        out += [(at(mid), "EXIT"), (at(mid + rng.uniform(4, 25)), "ENTRY")]
    leave = end - rng.uniform(35, 120) if rng.random() < p_early else end + abs(rng.gauss(4, 4))
    out.append((at(leave), "EXIT"))
    return sorted(out)


def rewind(now: datetime) -> int:
    """Apaga o que há de FICTÍCIO depois de `now` (passagens, avisos, alertas, frequência de dias futuros)."""
    cut = now.strftime(FMT)
    sub = "SELECT id FROM students WHERE ficticio = 1"
    with get_db_connection() as conn:
        n = conn.execute(f"DELETE FROM presences WHERE created_at > ? AND student_id IN ({sub})", (cut,)).rowcount
        for sql in (f"DELETE FROM notification_log WHERE created_at > ? AND student_id IN ({sub})",
                    "DELETE FROM security_events WHERE ficticio = 1 AND created_at > ?"):
            try:
                conn.execute(sql, (cut,))
            except Exception as e:          # tabela ausente em bancos antigos
                logger.debug(f"Limpeza do futuro simulado: {e}")
        conn.execute(f"DELETE FROM attendance_days WHERE day > ? AND student_id IN ({sub})", (now.date().isoformat(),))
        conn.commit()
    try:
        from database import attendance
        attendance.recompute_range(now.date(), now.date(), now=now)
    except Exception as e:
        logger.debug(f"Frequência de hoje não recalculada: {e}")
    logger.warning(f"Relógio voltou no tempo: {n} passagem(ns) fictícia(s) depois de {cut} removidas; "
                   "a simulação continua a partir de agora.")
    return n


class SimulationLoop:
    def __init__(self, engine=None):
        self.engine = engine
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        self.last_run: Dict[str, Any] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="simulacao")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(5)
        while not self._stop.is_set():
            try:
                self.run_once()
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Simulação ao vivo falhou: {e}")
            self._stop.wait(EVERY_SEC)

    # ------------------------------------------------------------------
    def _students(self) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT s.id, s.name, s.registration_number, s.class_name, s.created_at, "
                "(SELECT GROUP_CONCAT(g.telegram_chat_id || '|' || g.name || ' (' || sg.relationship || ')', ';') "
                " FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id "
                " WHERE sg.student_id = s.id AND g.telegram_chat_id LIKE 'fic-%') AS chats "
                "FROM students s WHERE s.ficticio = 1")]

    def run_once(self, now: Optional[datetime] = None) -> Dict[str, int]:
        from database import dados_ficticios
        if not dados_ficticios.LOCK.acquire(blocking=False):
            return {"passagens": 0, "dias": 0, "alertas": 0}       # gerando/removendo agora: tenta na próxima
        try:
            return self._run(now)
        finally:
            dados_ficticios.LOCK.release()

    def _run(self, now: Optional[datetime] = None) -> Dict[str, int]:
        from database import dados_ficticios, school
        now = (now or datetime.now()).replace(microsecond=0)
        st = get_state()
        out = {"passagens": 0, "dias": 0, "alertas": 0}
        if not st.get("ligada", True):
            return out
        students = self._students()
        if not students:
            return out
        since_txt = st.get("ate") or dados_ficticios.get_state().get("em") or now.strftime(FMT)
        try:
            since = datetime.strptime(since_txt[:19], FMT)
        except ValueError:
            since = now
        since = max(since, now - timedelta(days=MAX_CATCHUP_DAYS))
        if since > now + timedelta(minutes=CLOCK_BACK_MIN):
            # O relógio voltou (fuso corrigido: a AIBOX estava em UTC/China). O que foi simulado
            # "no futuro" sai, e a rotina continua daqui — os mesmos horários voltam na hora certa.
            out["removidas"] = rewind(now)
            set_state(ate=now.strftime(FMT))
            self.last_run = dict(out, em=now.strftime(FMT))
            return out
        if since >= now:
            return out
        cal = school.Calendar.load()
        cam = int(getattr(getattr(self.engine, "settings", None), "camera_id", 1) or 1)
        rows, logs = [], []
        # última direção de cada aluno fictício por dia (o que já está no banco antes de `since`):
        # evita duas entradas seguidas quando a geração e a simulação se encontram no mesmo dia.
        last_dir: Dict[Tuple[int, str], str] = {}
        with get_db_connection() as conn:
            for r in conn.execute("SELECT student_id, date(created_at) AS d, direction FROM presences "
                                  "WHERE created_at >= ? AND created_at <= ? AND student_id IN "
                                  "(SELECT id FROM students WHERE ficticio = 1) ORDER BY created_at, id",
                                  (since.date().isoformat(), since.strftime(FMT))):
                last_dir[(r[0], r[1])] = r[2]
        d = since.date()
        days_done: List[date] = []
        while d <= now.date():
            for s in students:
                chats = [c.split("|", 1) for c in (s.get("chats") or "").split(";") if "|" in c]
                shift = cal.shift_for_class(s.get("class_name") or "")
                for when, direction in plan_day(s, d, cal):
                    if not (since < when <= now):
                        continue
                    prev = last_dir.get((s["id"], d.isoformat()))
                    if direction == prev or (direction == "EXIT" and prev is None):
                        continue        # mantém a sequência entrada → saída → entrada…
                    last_dir[(s["id"], d.isoformat())] = direction
                    rows.append((s["id"], s["name"], s["registration_number"], cam, 0,
                                 round(random.Random(f"{s['id']}{when}").uniform(0.52, 0.83), 3), direction,
                                 "SENT" if chats else "NO_CHAT_ID",
                                 "Envio simulado (dado fictício)." if chats else "Registro fictício (sem Telegram).",
                                 when.strftime(FMT)))
                    kind = "EXIT"
                    if direction == "ENTRY":
                        late = shift and when.hour * 60 + when.minute > school.minutes(shift["deadline"])
                        kind = "LATE" if late else "ENTRY"
                    for chat, label in chats:
                        logs.append((s["id"], s["name"], label, chat, kind, "SENT", "", "",
                                     (when + timedelta(seconds=3)).strftime(FMT)))
            days_done.append(d)
            d += timedelta(days=1)
        rows.sort(key=lambda r: r[-1])
        with get_db_connection() as conn:
            if rows:
                conn.executemany("INSERT INTO presences (student_id, student_name, registration_number, camera_id, "
                                 "track_id, similarity, direction, telegram_status, telegram_detail, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            if logs:
                try:
                    conn.executemany("INSERT INTO notification_log (student_id, student_name, guardian, chat_id, kind, "
                                     "status, detail, sent_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", logs)
                except Exception as e:          # tabela do histórico ausente em bancos antigos: segue sem ela
                    logger.debug(f"Histórico de avisos simulado não gravado: {e}")
            conn.commit()
        out["passagens"], out["dias"] = len(rows), len(days_done)
        # dias inteiros que ficaram para trás (sistema desligado): recalcula a frequência deles
        past = [x for x in days_done if x < now.date()]
        if rows and past:
            from database import attendance
            attendance.recompute_range(min(past), max(past), now=now)
        live = (now - since) <= timedelta(minutes=10)
        if live:
            out["alertas"] = self._maybe_alert(now, since, cal, cam)
        set_state(ate=now.strftime(FMT))
        self.last_run = dict(out, em=now.strftime(FMT))
        if rows and self.engine is not None:
            try:
                from app.security_alerts import request_panel_update
                request_panel_update()
            except Exception:
                pass
        return out

    def _maybe_alert(self, now: datetime, since: datetime, cal, cam: int) -> int:
        """Alerta fictício de rosto obstruído, de vez em quando, no horário de aula (no máx. 1 pendente)."""
        from database import repository, school
        if not cal.day_status(now.date(), cal.default_shift)[0]:
            return 0
        minutes = max(0.0, (now - since).total_seconds() / 60)
        if random.random() > minutes / ALERT_EVERY_MIN:
            return 0
        shifts = [s for s in cal.shifts.values()
                  if school.minutes(s["start_time"]) - 20 <= now.hour * 60 + now.minute <= school.minutes(s["end_time"])]
        if not shifts:
            return 0
        with get_db_connection() as conn:
            if conn.execute("SELECT 1 FROM security_events WHERE ficticio = 1 AND status = 'PENDING'").fetchone():
                return 0
        eid = repository.save_security_event({"event_type": "FACE_OBSTRUCTED", "camera_id": cam, "severity": "RED",
                                              "track_id": random.randint(1, 900),
                                              "description": "Rosto obstruído (fictício)",
                                              "detection_confidence": round(random.uniform(0.55, 0.9), 2),
                                              "status": "PENDING"}, return_id=True)
        if not eid:
            return 0
        with get_db_connection() as conn:
            conn.execute("UPDATE security_events SET ficticio = 1 WHERE id = ?", (eid,))
            conn.commit()
        try:
            from database.dados_ficticios import _snapshot_jpeg
            photo = _snapshot_jpeg()
            if photo:
                repository.save_event_snapshot(eid, photo)
        except Exception:
            pass
        return 1
