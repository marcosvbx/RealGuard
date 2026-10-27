"""
Registro de presença + gatilho do Telegram.

Regra (mesma ideia do EduVision): quando uma pessoa passa pela câmera e
sua identidade se estabiliza (RESOLVED), registra-se UMA presença por
aparição. Esse registro é o gatilho do aviso no Telegram.

Duas proteções contra mensagem repetida:
  1. por track: um track_id só gera presença uma vez;
  2. por aluno: cooldown (PRESENCE_COOLDOWN_SEC) — o rastreador pode trocar
     o track_id de quem fica parado/oculto e voltar, e sem isso o pai
     receberia a mesma mensagem várias vezes.
"""

import logging
import threading
import time
from config.clock import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from app.notifications.messages import _minutes, presence_message, security_message
from database import notifications as nlog
from database import repository

logger = logging.getLogger(__name__)

STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"
STATUS_NO_CHAT_ID = "NO_CHAT_ID"
STATUS_DISABLED = "DISABLED"
STATUS_QUEUE_FULL = "QUEUE_FULL"


def format_message(template: str, student: Dict[str, Any], camera_name: str, direction: str = "ENTRY") -> str:
    now = datetime.now()
    values = {
        "nome": student.get("name", ""),
        "rm": student.get("registration_number", "") or "",
        "hora": now.strftime("%H:%M"),
        "data": now.strftime("%d/%m/%Y"),
        "camera": camera_name,
        "acao": "saiu" if direction == "EXIT" else "entrou",
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        # Template do usuário com variável desconhecida: não perde o aviso.
        return f"{values['nome']} foi identificado(a) pela câmera às {values['hora']} de {values['data']}."


def student_chats(student: Dict[str, Any]) -> List[str]:
    """Chats do Telegram de TODOS os responsáveis vinculados (cache do motor) — ou o campo antigo."""
    many = student.get("telegram_chats")
    if many:
        return [c for c in dict.fromkeys(str(many).split(",")) if c.strip()]
    one = (student.get("telegram_chat_id") or "").strip()
    return [one] if one else []


def early_exit_line(student: Dict[str, Any], html: bool = True, now: Optional[datetime] = None) -> str:
    """
    Saída antes do fim do turno: a própria mensagem automática de saída já avisa
    (não precisa de um segundo aviso manual da portaria).
    """
    try:
        from database import school
        shift = school.Calendar.load().shift_for_class(student.get("class_name") or "")
    except Exception:
        return ""
    if not shift or not shift.get("end_time"):
        return ""
    now = now or datetime.now()
    if now.hour * 60 + now.minute >= school.minutes(shift["end_time"]):
        return ""
    if now.hour * 60 + now.minute < school.minutes(shift["start_time"]):
        return ""
    end = shift["end_time"]
    return (f"\n🟠 <b>Antes do fim do turno</b> (aulas até {end})" if html else f"\nAntes do fim do turno (aulas até {end})")


def pickup_line(student_id: int, html: bool = True) -> str:
    """Saída num dia com retirada autorizada pelo responsável: a mensagem diz para quem."""
    try:
        from database import pickups
        rows = pickups.active_for_day(student_id)
    except Exception:
        return ""
    if not rows:
        return ""
    from html import escape
    who = ", ".join(f"{r['person_name']} ({r['relationship']})" for r in rows)
    return f"\n🚗 Retirada autorizada hoje: {escape(who) if html else who}"


class PresenceService:
    def __init__(
        self,
        camera_id: int,
        camera_name: str = "Camera",
        notifier=None,
        message_template: str = "{nome} foi identificado(a) às {hora}.",
        cooldown_sec: float = 30.0,
        security_chat_ids: Optional[list] = None,
        security_cooldown_sec: float = 60.0,
        entry_tolerance: str = "",
        clock: Callable[[], float] = time.monotonic,
    ):
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.notifier = notifier
        self.message_template = message_template
        self.cooldown_sec = cooldown_sec
        self._clock = clock
        self.entry_tolerance = entry_tolerance
        # ENTRY | EXIT | AUTO (Fase 1): função da câmera ativa. Em AUTO a direção é
        # decidida por aluno: 1ª passagem do dia = entrada; a próxima, depois do
        # intervalo mínimo (Escola > Regras), = saída; e assim alternando.
        self.mode = "ENTRY"
        self._gap_cache = (-1e9, 30 * 60.0)      # força a 1ª leitura
        self.security_chat_ids = security_chat_ids or []
        # Chave geral (Escola > Integrações > Telegram): False = nenhum aviso aos responsáveis.
        self.guardians_enabled = True
        self.security_cooldown_sec = security_cooldown_sec
        self._last_security: Optional[float] = None
        self._lock = threading.Lock()
        self._tracks_done: Set[int] = set()
        self._last_by_student: Dict[int, float] = {}
        self.last_presence: Optional[Dict[str, Any]] = None
        # Última leitura da câmera (registrada OU ignorada e por quê) — o card "Agora na câmera"
        # mostra, para ninguém achar que "não registrou" quando foi o intervalo mínimo.
        self.last_read: Optional[Dict[str, Any]] = None
        self._gap_left_min = 0

    def handle_resolved(
        self,
        student: Dict[str, Any],
        track_id: int,
        similarity: float = 0.0,
        photo_fn: Optional[Callable[[], bytes]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Chamado a cada frame para tracks RESOLVED; é barato quando já foi
        tratado. Retorna o registro criado, ou None se nada foi registrado.
        """
        student_id = student.get("id")
        now = self._clock()

        with self._lock:
            if track_id in self._tracks_done:
                return None
            self._tracks_done.add(track_id)

        direction = self._direction_for(student_id)
        if direction is None:
            # modo Automático: passou de novo logo após a última passagem (circulação)
            self._read(student, "intervalo", None, self._gap_left_min)
            return None
        key = (student_id, direction)
        with self._lock:
            last = self._last_by_student.get(key)
            if last is not None and (now - last) < self.cooldown_sec:
                self._read(student, "repetida", direction)
                return None
            self._last_by_student[key] = now

        chats = student_chats(student)
        muted = False
        if chats and student_id is not None:
            # 🔔 "O que receber" no bot: cada responsável escolhe entrada e/ou saída.
            try:
                from database import guardians as gdb
                if gdb.chat_ids_for_student(student_id):
                    wanted = gdb.chats_for_notice(student_id, "saida" if direction == "EXIT" else "entrada")
                    muted = not wanted
                    chats = wanted
            except Exception as e:
                logger.debug(f"Preferências de aviso não lidas: {e}")
        chat_id = chats[0] if chats else ""
        if not self.notifier or not self.notifier.enabled:
            status, detail = STATUS_DISABLED, "Token do Telegram não configurado."
        elif not self.guardians_enabled:
            status, detail = STATUS_DISABLED, "Avisos aos responsáveis desligados na tela do Telegram."
        elif muted:
            status, detail = STATUS_DISABLED, "Os responsáveis desligaram este aviso no bot."
        elif not chat_id:
            status, detail = STATUS_NO_CHAT_ID, "Aluno sem Telegram Chat ID."
        else:
            status, detail = "PENDING", ""

        presence_id = repository.register_presence(
            student_id=student_id,
            student_name=student.get("name", ""),
            registration_number=student.get("registration_number"),
            camera_id=self.camera_id,
            track_id=track_id,
            similarity=similarity,
            telegram_status=status,
            telegram_detail=detail,
            direction=direction,
        )
        logger.info(f"Presença registrada: {student.get('name')} (track {track_id}).")

        if status == "PENDING":
            if self.message_template:   # template próprio (texto simples)
                text, parse = format_message(self.message_template, student, self.camera_name, direction), None
            else:                       # padrão: mensagem formatada
                text = presence_message(student, self.camera_name, direction, self._deadline_for(student))
                parse = "HTML"
            if direction == "EXIT" and student_id is not None:
                text += early_exit_line(student, html=parse == "HTML")
                text += pickup_line(student_id, html=parse == "HTML")
            photo = photo_fn() if photo_fn else None

            # Vários responsáveis: o registro fica "enviado" se ao menos um recebeu.
            results: List[bool] = []
            total = len(chats)

            def _done(ok: bool, info: str, pid=presence_id):
                results.append(ok)
                if pid is None or len(results) < total:
                    return
                n_ok = sum(results)
                detail = info if total == 1 else f"{n_ok} de {total} responsáveis avisados"
                repository.update_presence_telegram(pid, STATUS_SENT if n_ok else STATUS_FAILED, detail)

            # histórico de avisos (Atividades recentes / Ocorrências): uma linha por responsável, atualizada quando o Telegram responder
            kind = "EXIT" if direction == "EXIT" else "ENTRY"
            if kind == "ENTRY":
                deadline = self._deadline_for(student)
                t = datetime.now()
                if deadline and t.hour * 60 + t.minute > _minutes(deadline):
                    kind = "LATE"

            def _send(c):
                lid = nlog.log(student_id, student.get("name", ""), kind, chat_id=c)

                def _cb(ok: bool, info: str):
                    nlog.update(lid, nlog.SENT if ok else nlog.FAILED, "" if ok else info)
                    _done(ok, info)
                ok = self.notifier.enqueue(c, text, _cb, photo=photo, parse_mode=parse)
                if not ok:
                    nlog.update(lid, nlog.FAILED, "Fila de envio cheia.")
                return ok

            queued = [_send(c) for c in chats]
            if not any(queued):
                status = STATUS_QUEUE_FULL
                if presence_id is not None:
                    repository.update_presence_telegram(presence_id, status, "Fila de envio cheia.")

        record = {
            "presence_id": presence_id,
            "student_id": student_id,
            "name": student.get("name"),
            "track_id": track_id,
            "direction": direction,
            "telegram_status": status,
            "at": datetime.now().strftime("%H:%M:%S"),
        }
        self.last_presence = record
        self._read(student, "registrada", direction)
        return record

    # ---------------------------------------------------------------- Fase 1
    def _auto_gap_sec(self) -> float:
        """Intervalo mínimo entre passagens no modo Automático (Escola > Regras), relido a cada 60 s."""
        checked, value = self._gap_cache
        if time.monotonic() - checked > 60:
            try:
                from database import school
                value = float(school.get_settings().get("auto_exit_min_minutes") or 30) * 60
            except Exception:
                pass
            self._gap_cache = (time.monotonic(), value)
        return value

    def _read(self, student: Dict[str, Any], result: str, direction: Optional[str], wait_min: int = 0) -> None:
        self.last_read = {"nome": student.get("name", ""), "student_id": student.get("id"), "resultado": result,
                          "direcao": direction, "espera_min": wait_min, "hora": datetime.now().strftime("%H:%M:%S"),
                          "ts": time.time()}

    def _direction_for(self, student_id) -> Optional[str]:
        if self.mode != "AUTO":
            return self.mode
        if student_id is None:
            return "ENTRY"
        try:
            last = repository.last_passage_today(student_id)
        except Exception as e:
            logger.error(f"Modo automático: não consegui ler a última passagem ({e}). Registrando entrada.")
            return "ENTRY"
        if not last:
            return "ENTRY"
        try:
            elapsed = (datetime.now() - datetime.strptime(last["created_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
        except (TypeError, ValueError):
            elapsed = float("inf")
        gap = self._auto_gap_sec()
        if elapsed < gap:
            self._gap_left_min = max(1, int((gap - elapsed) // 60) + 1)
            return None
        return "EXIT" if last["direction"] == "ENTRY" else "ENTRY"

    def _deadline_for(self, student: Dict[str, Any]) -> str:
        """Horário-limite de entrada do turno da turma do aluno (cai no ENTRY_TOLERANCE se não houver turnos)."""
        try:
            from database import school
            return school.deadline_for_class(student.get("class_name") or "") or self.entry_tolerance
        except Exception:
            return self.entry_tolerance

    def notify_security(self, track_id: int, photo_fn: Optional[Callable[[], bytes]] = None, force: bool = False,
                        event_id: Optional[int] = None) -> str:
        """
        1º aviso de rosto obstruído para a segurança: CURTO e SEM foto, pedindo a decisão no
        RealGuard. A foto (guardada no alerta) só vai no relatório, depois da confirmação humana.
        Com o alerta gravado (event_id) e o bot de segurança rodando, o aviso é o CARTÃO com
        botões, enviado pela thread do painel (security_alerts.SecurityPanel.sync_cards).
        """
        from app import security_alerts
        if event_id is not None and security_alerts.PANEL is not None:
            if not security_alerts.sec_notifier(security_alerts.PANEL.engine).enabled:
                return "DISABLED"
            if not security_alerts.recipients(security_alerts.PANEL.engine):
                return "NO_RECIPIENTS"
            security_alerts.request_panel_update()
            return "CARD"
        now = self._clock()
        if not force and self._last_security is not None and now - self._last_security < self.security_cooldown_sec:
            return "COOLDOWN"
        if not self.notifier or not self.notifier.enabled:
            return "DISABLED"
        chats = list(self.security_chat_ids) or [r["chat"] for r in security_alerts.recipients(self)]
        if not chats:
            return "NO_RECIPIENTS"
        self._last_security = now
        from app.security_alerts import dashboard_link
        text = security_message(self.camera_name, link=dashboard_link())
        for chat_id in chats:
            self.notifier.enqueue(chat_id, text, None, parse_mode="HTML")
        from app.security_alerts import request_panel_update
        request_panel_update()
        return "SENT"

    def forget_track(self, track_id: int) -> None:
        with self._lock:
            self._tracks_done.discard(track_id)
