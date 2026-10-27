"""
Motor do RealGuard: dono do ciclo de vida da IA.

É o que faz o sistema rodar 100% do tempo, independente do dashboard: sobe
junto com o servidor (ver api/main.py), roda em threads próprias e se
recupera sozinho. O dashboard é só um cliente que olha o que já está
acontecendo — abrir, fechar ou trocar de aba não interfere na captura,
no reconhecimento, no registro de presença nem no Telegram.
"""

import logging
import os
import threading
import time
from config.clock import datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np

from app.notifications.telegram import TelegramNotifier
from app.notifications.telegram_link import TelegramLinker
from app.pipeline import RealGuardPipeline, placeholder_jpeg
from app.presence import PresenceService
from config.settings import Settings
from config.settings import clock_adjust_minutes as _clock_adjust, clock_adjust_days as _date_adjust
from database.migrations import initialize_database

logger = logging.getLogger(__name__)

STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"


class Engine:
    RETRY_DELAY_SEC = 10

    def __init__(self):
        self.settings = Settings()
        self.state = STATE_STOPPED
        self.error: Optional[str] = None
        self.notice: Optional[str] = None
        self.mode = "AUTO"     # função da câmera ativa (cameras.direction); lida na subida do pipeline
        self.mirror = self.settings.mirror_video
        self.pipeline: Optional[RealGuardPipeline] = None
        self.notifier = TelegramNotifier(self.settings.telegram_token)
        # Telegram configurável pela tela (banco) — o .env é a reserva. Ver apply_telegram().
        self.telegram = {"token_origem": "env" if self.settings.telegram_token else "",
                         "avisos_responsaveis": True, "seguranca": list(self.settings.security_chat_ids),
                         "seguranca_origem": "env", "modelo": self.settings.telegram_template}
        self.started_at: Optional[float] = None
        # Bot que vincula o responsável sozinho (convite ou número compartilhado).
        self.linker = TelegramLinker(self.notifier, on_linked=self.refresh_students)
        from app.guardian_bot import GuardianBot
        from app.security_bot import SecurityBot
        self.guardian_bot = GuardianBot(self)
        self.security_bot = SecurityBot(self)
        self.linker.guardian_bot = self.guardian_bot
        self.linker.security_bot = self.security_bot
        # Bot SÓ da segurança (token próprio). Sem ele, os alertas saem pelo bot dos responsáveis.
        self.sec_notifier = TelegramNotifier(self.settings.security_bot_token)
        self.sec_linker = TelegramLinker(self.sec_notifier, mode="seguranca")
        self.sec_linker.security_bot = self.security_bot
        self.presence: Optional[PresenceService] = None
        self._stop = threading.Event()
        # Acorda o supervisor antes do intervalo de nova tentativa (troca de câmera).
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._switch_lock = threading.Lock()

    # ------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        """Não bloqueia: modelos pesados carregam em segundo plano."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = STATE_STARTING
        self.started_at = time.time()
        try:
            self.apply_telegram(restart_linker=False)
        except Exception as e:      # banco ainda indisponível: segue com o .env
            logger.warning(f"Configuração do Telegram do banco não lida: {e}")
        from app.arena import enabled
        if not enabled():
            self.notifier.start()
            self.sec_notifier.start()
            self.linker.start()
            if self.security_separate:
                self.sec_linker.start()
        self._thread = threading.Thread(target=self._supervise, daemon=True, name="engine")
        self._thread.start()

    def stop(self) -> None:
        self.request_stop()
        if self.pipeline:
            self.pipeline.stop()
        self.linker.stop()
        self.sec_linker.stop()
        self.notifier.stop()
        self.sec_notifier.stop()
        self.state = STATE_STOPPED

    def request_stop(self) -> None:
        """Sinaliza câmera e MJPEG antes de aguardar o encerramento das conexões."""
        self._stop.set()
        self._wake.set()
        if self.pipeline:
            self.pipeline.is_running = False
            if self.pipeline.camera:
                self.pipeline.camera.is_running = False

    def _build_pipeline(self, use_face: bool) -> RealGuardPipeline:
        s = self.settings
        return RealGuardPipeline(
            camera_id=s.camera_id,
            stream_url=s.rtsp_url,
            model_path=s.model_path,
            use_face_recognition=use_face,
            presence_service=self.presence,
            async_recognition=True,
            face_model=s.face_model,
            face_threshold=s.face_threshold,
            max_fps=s.max_fps,
            process_max_width=s.process_max_width,
            detection_interval_sec=s.detection_interval_sec,
            yolo_threads=s.yolo_threads,
            yolo_confidence=s.yolo_confidence,
            face_threads=s.face_threads,
            recognition_interval_sec=s.recognition_interval_sec,
            resolved_recheck_sec=s.resolved_recheck_sec,
            face_soft_threshold=s.face_soft_threshold,
            track_timeout_sec=s.track_timeout_sec,
            obstructed_min_sec=s.obstructed_min_sec,
            obstructed_min_attempts=s.obstructed_min_attempts,
            obstructed_min_person_height=s.obstructed_min_person_height,
            mirror=self.mirror,
        )

    def _supervise(self) -> None:
        """Sobe o pipeline; se falhar na inicialização, tenta de novo (não morre)."""
        s = self.settings
        while not self._stop.is_set():
            try:
                initialize_database(s.camera_id, s.camera_name)
                self.apply_telegram()
                s.validate()
                self.presence = PresenceService(
                    camera_id=s.camera_id,
                    camera_name=s.camera_name,
                    notifier=self.notifier,
                    message_template=self.telegram["modelo"],
                    cooldown_sec=s.presence_cooldown_sec,
                    security_cooldown_sec=s.security_cooldown_sec,
                    entry_tolerance=s.entry_tolerance,
                )
                self.presence.guardians_enabled = self.telegram["avisos_responsaveis"]
                from database import repository
                self.mode = repository.get_camera_direction(s.camera_id)
                self.presence.mode = self.mode
                self.notice = None
                try:
                    self.pipeline = self._build_pipeline(s.use_face_recognition)
                except Exception as e:
                    if not s.use_face_recognition:
                        raise
                    # InsightFace indisponível (pacote/modelo): segue só com detecção.
                    logger.error(f"Reconhecimento facial indisponível ({e}). Seguindo sem ele.")
                    self.notice = f"Reconhecimento facial desativado: {e}"
                    self.pipeline = self._build_pipeline(False)

                self.pipeline.refresh_students()
                if self._stop.is_set():
                    break
                if self._wake.is_set():      # trocaram a câmera enquanto os modelos carregavam
                    self._wake.clear()
                    continue
                self.error = None
                self.state = STATE_RUNNING
                logger.info("RealGuard em execução. Config: %s", s.get_safe_summary())
                self.pipeline._external_stop = self._stop
                self.pipeline.start()          # bloqueia até stop()
                if self._stop.is_set():
                    break
                if self._wake.is_set():
                    self._wake.clear()
                    logger.info("Reiniciando o pipeline com a nova câmera...")
                    continue
                logger.warning("Pipeline encerrou sozinho. Reiniciando...")
            except Exception as e:
                self.state = STATE_ERROR
                self.error = str(e)
                logger.error(f"Falha ao iniciar o motor: {e}")
            self._wake.wait(self.RETRY_DELAY_SEC)
            self._wake.clear()

    # ------------------------------------------------------------ helpers p/ API
    @property
    def ready(self) -> bool:
        return self.state == STATE_RUNNING and self.pipeline is not None

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if self.presence:
            self.presence.mode = mode

    def switch_camera(self, camera_id: int, name: str, source: str, kind: str) -> None:
        """
        Troca a câmera do pipeline sem reiniciar o servidor (teste no dia da
        banca). Continua sendo UM pipeline só: a câmera anterior é desligada.
        Vale até reiniciar o sistema, a menos que seja gravada no realguard.env.
        """
        with self._switch_lock:
            s = self.settings
            s.camera_id, s.camera_name, s.rtsp_url = int(camera_id), name, source
            s.camera_mode = "rtsp" if kind == "rtsp" else "webcam"
            # espelhamento automático acompanha o tipo da nova câmera (webcam x IP)
            from config.settings import resolve_mirror
            s.mirror_video = self.mirror = resolve_mirror(s.mirror_setting, s.camera_mode, source)
            self.state = STATE_STARTING
            self._wake.set()
            pipeline = self.pipeline
        logger.info("Trocando para a câmera %s (%s).", name, kind)
        if pipeline:
            # stop() espera as threads de IA (até ~6 s): fora da requisição HTTP.
            threading.Thread(target=pipeline.stop, daemon=True, name="troca-camera").start()

    def apply_telegram(self, restart_linker: bool = True) -> Dict[str, Any]:
        """Lê a configuração do Telegram (banco > .env) e aplica na hora, sem reiniciar."""
        from app.arena import enabled
        if enabled():
            self.notifier.set_token("")
            self.sec_notifier.set_token("")
            self.telegram.update(avisos_responsaveis=False, seguranca=[])
            return self.telegram
        from database import system_settings
        s = self.settings
        cfg = system_settings.telegram_config(s.telegram_token, s.security_chat_ids, s.telegram_template,
                                              s.security_bot_token)
        changed = cfg["token"] != self.notifier.token
        if changed:
            self.notifier.set_token(cfg["token"])
        sec_changed = cfg["sec_token"] != self.sec_notifier.token
        if sec_changed:
            self.sec_notifier.set_token(cfg["sec_token"])
        self.telegram = {k: v for k, v in cfg.items() if k not in ("token", "sec_token")}
        # Mesmo bot para os dois (sem token da segurança, ou o mesmo token): o linker dos
        # responsáveis também atende os botões dos alertas; senão, cada bot no seu linker.
        self.linker.security_bot = None if self.security_separate else self.security_bot
        if restart_linker and (sec_changed or changed):
            if self.security_separate:
                self.sec_linker.restart()
            else:
                self.sec_linker.stop()
        if self.presence:
            self.presence.message_template = cfg["modelo"]
            self.presence.guardians_enabled = cfg["avisos_responsaveis"]
        if changed and restart_linker:
            self.linker.restart()
        return self.telegram

    @property
    def security_separate(self) -> bool:
        """True quando a segurança tem um bot próprio (token diferente do bot dos responsáveis)."""
        return self.sec_notifier.enabled and self.sec_notifier.token != self.notifier.token

    @property
    def security_notifier(self):
        """Bot que envia e recebe os alertas de segurança."""
        return self.sec_notifier if self.security_separate else self.notifier

    def _security_count(self) -> int:
        """Quantas pessoas recebem os alertas de segurança no Telegram (contas vinculadas)."""
        try:
            from app.security_alerts import recipients
            return len(recipients(self))
        except Exception:
            return 0

    @property
    def guardians_enabled(self) -> bool:
        return bool(self.telegram.get("avisos_responsaveis", True))

    def refresh_students(self) -> None:
        """Dados do aluno mudaram (ex.: Telegram vinculado) sem mexer nos rostos."""
        if self.pipeline:
            self.pipeline.refresh_students()

    def reload_faces(self) -> None:
        if self.pipeline:
            self.pipeline.reload_known_embeddings()

    def face_recognizer(self):
        return self.pipeline.face_recognizer if self.pipeline else None

    def snapshot(self) -> Optional[np.ndarray]:
        return self.pipeline.snapshot() if self.pipeline else None

    def status(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "sistema": "RealGuard",
            "estado": self.state,
            "erro": self.error,
            "aviso": self.notice,
            "modo": self.mode,
            "espelhado": self.mirror,
            "seguranca_destinatarios": self._security_count(),
            "camera_id": self.settings.camera_id,
            "camera_nome": self.settings.camera_name,
            "telegram": {
                "configurado": self.notifier.enabled,
                "bot": self.notifier.get_bot_username(block=False) if self.notifier.enabled else None,
                "erro": self.linker.last_error,
                "avisos_responsaveis": self.guardians_enabled,
            },
            "camera_modo": self.settings.camera_mode,
            # Relógio do dispositivo: o dashboard compara com o do navegador e
            # avisa se estiver errado (horário de presença depende dele).
            "relogio": {"epoch": round(time.time(), 1),
                        "local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "fuso": time.strftime("%z"), "ajuste_min": _clock_adjust(), "ajuste_dias": _date_adjust()},
            "tolerancia": self.settings.entry_tolerance,
            "ultima_presenca": self.presence.last_presence if self.presence else None,
            "ultima_leitura": self.presence.last_read if self.presence else None,
        }
        if self.pipeline:
            base.update(self.pipeline.get_status())
        from app.arena import enabled, arena
        base["arena"] = enabled()
        if enabled():
            base["arena_clock"] = arena.now().isoformat(sep=" ", timespec="seconds")
            arena.metrics(base)
        return base

    def mjpeg_stream(self):
        """Stream MJPEG. Antes do pipeline ficar pronto, mostra um quadro de espera."""
        while not self._stop.is_set():
            if self.ready:
                yield from self.pipeline.mjpeg_stream()
                time.sleep(0.5)  # pipeline parou; evita laço apertado até reiniciar
                continue
            msg = "Iniciando modelos..." if self.state == STATE_STARTING else "Motor indisponivel"
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + placeholder_jpeg(msg) + b"\r\n")
            time.sleep(1.0)


engine = Engine()
