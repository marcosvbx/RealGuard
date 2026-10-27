"""
Rotinas de manutenção em segundo plano (thread leve, acorda a cada 30 min):

* backup automático do banco + fotos a cada BACKUP_INTERVAL_HOURS (padrão 24 h);
* limpeza de sessões expiradas;
* limpeza da auditoria mais antiga que AUDIT_RETENTION_DAYS (padrão 365);
* Fase 1: fecha a frequência dos dias anteriores (presença consolidada),
  semeia os feriados nacionais do ano novo e aplica a retenção (LGPD) da
  tela Escola > Regras: fotos de alertas e passagens brutas antigas.

Não toca no pipeline de visão: o custo é um backup por dia.
"""

import logging
import os
import threading
from datetime import datetime, timedelta

from database import access, attendance, backup, school

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class Maintenance:
    CHECK_EVERY_SEC = 1800

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="manutencao")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Primeira rodada um pouco depois da subida (o banco é migrado pelo motor).
        if self._stop.wait(60):
            return
        while True:
            self.run_once()
            if self._stop.wait(self.CHECK_EVERY_SEC):
                return

    def backup_due(self) -> bool:
        if os.getenv("BACKUP_AUTO", "true").strip().lower() not in ("1", "true", "yes", "on"):
            return False
        last = backup.latest("auto")
        if last is None:
            return True
        hours = max(1.0, _float_env("BACKUP_INTERVAL_HOURS", 24.0))
        created = datetime.strptime(last["criado_em"], "%Y-%m-%d %H:%M:%S")
        return datetime.now() - created >= timedelta(hours=hours)

    def run_once(self) -> None:
        try:
            if self.backup_due():
                backup.create_backup("auto")
            access.purge_expired_sessions()
            self.run_school_rules()
            access.purge_old_audit(int(_float_env("AUDIT_RETENTION_DAYS", 365)))
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Manutenção (backup/limpeza) falhou: {e}")

    @staticmethod
    def run_school_rules() -> None:
        from database.connection import get_db_connection
        with get_db_connection() as conn:
            school.ensure_national_holidays(conn)
            conn.commit()
        closed = attendance.close_pending_days()
        settings = school.get_settings()
        photos = attendance.purge_old_event_photos(int(settings.get("retention_photo_days") or 30))
        passages = attendance.purge_old_passages(int(settings.get("retention_passages_days") or 180))
        from database import pickups
        pickup_photos = pickups.purge_photos()
        if pickup_photos:
            logger.info(f"Retenção: {pickup_photos} foto(s) de retiradas apagadas (7 dias depois da data).")
        if closed or photos or passages:
            logger.info(f"Frequência: {closed} dia(s) fechado(s). Retenção: {photos} foto(s) de alerta e "
                        f"{passages} passagem(ns) antigas apagadas.")
            if photos or passages:
                access.log_audit("retencao_aplicada", target_type="sistema", username="sistema",
                                 detail=f"{photos} foto(s), {passages} passagem(ns)")


maintenance = Maintenance()
