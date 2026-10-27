import cv2
import os
import threading
import time
import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


class BufferlessCameraCapture:
    """
    Leitor de stream (RTSP/USB) otimizado para Edge AI.
    Mantém estritamente apenas o frame mais recente em memória,
    descartando frames não lidos para evitar gargalos de RAM e latência.

    A câmera é aberta DENTRO da thread de captura: o construtor nunca
    bloqueia (um RTSP fora do ar pode levar dezenas de segundos para
    falhar) e a reconexão é automática, sem derrubar o sistema.
    """

    # Se uma única leitura demorar mais que isso, o FFmpeg represou pacotes:
    # reabrir a captura descarta o buffer e volta ao tempo real.
    READ_TIMEOUT_SEC = 3.0
    # Sem frame novo por este tempo => câmera considerada offline.
    OFFLINE_AFTER_SEC = 3.0

    def __init__(self, source: str, camera_name: str = "Camera_01", reconnect_delay: int = 5):
        source = str(source)
        self.source = int(source) if source.isdigit() else source
        self.camera_name = camera_name
        self.reconnect_delay = reconnect_delay

        self.cap = None
        self.ret = False
        self.frame: Optional[np.ndarray] = None
        self.last_frame_time = 0.0
        self.fps = 0.0
        self.frame_seq = 0
        self.frame_monotonic = 0.0
        self.backend_name = 'opencv'
        self._gst_failed = False

        self.is_running = False
        self.read_lock = threading.Lock()
        self.thread = None

    def _open(self):
        mode = os.getenv("CAMERA_CAPTURE_BACKEND", "opencv").lower()
        if (mode in ("auto", "gstreamer") and not self._gst_failed
                and str(self.source).lower().startswith("rtsp://")):
            from app.camera.gstreamer import GstCameraCapture, device_access
            if device_access():
                codec = os.getenv("CAMERA_GST_CODEC", "auto").lower()
                codecs = ("h264", "h265") if codec == "auto" else (codec,)
                for candidate in codecs:
                    try:
                        cap = GstCameraCapture(self.source, codec=candidate,
                            decoder=os.getenv("CAMERA_GST_DECODER", "qtivdec"))
                        self.backend_name = cap.backend_name + ":" + candidate
                        return cap
                    except Exception as exc:
                        logger.warning("[%s] Decoder de hardware indisponivel (%s); tentando alternativa.",
                                       self.camera_name, type(exc).__name__)
            self._gst_failed = True
        self.backend_name = "opencv"
        if isinstance(self.source, str) and self.source.lower().startswith("rtsp://"):
            try:
                cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000])
            except (TypeError, cv2.error):
                cap = cv2.VideoCapture(self.source)
        else:
            cap = cv2.VideoCapture(self.source)
        if isinstance(self.source, int):
            # Webcam: 16:9 evita o driver cortar as laterais da imagem.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def start(self) -> 'BufferlessCameraCapture':
        """Inicia a thread de captura de frames em segundo plano."""
        if self.is_running:
            return self

        self.is_running = True
        self.thread = threading.Thread(target=self._update, daemon=True, name=f"Thread_{self.camera_name}")
        self.thread.start()
        logger.info(f"[{self.camera_name}] Thread de captura iniciada.")
        return self

    def _update(self) -> None:
        """Loop interno da thread. Lê a câmera o mais rápido possível e descarta o histórico."""
        frames = 0
        window_start = time.time()

        while self.is_running:
            if self.cap is None or not self.cap.isOpened():
                if self.cap is not None:
                    self.cap.release()
                self.cap = self._open()
                if not self.cap.isOpened():
                    logger.warning(f"[{self.camera_name}] Câmera indisponível. Nova tentativa em {self.reconnect_delay}s...")
                    self._sleep(self.reconnect_delay)
                    continue
                logger.info(f"[{self.camera_name}] Câmera conectada.")

            started = time.time()
            ret, frame = self.cap.read()
            took = time.time() - started

            if took > self.READ_TIMEOUT_SEC:
                if self.backend_name.startswith("gstreamer:"):
                    self._gst_failed = True
                logger.warning(f"[{self.camera_name}] Leitura travou {took:.1f}s. Reconectando para descartar buffer...")
                self.cap.release()
                self.cap = None
                continue

            if not ret:
                if self.backend_name.startswith("gstreamer:"):
                    self._gst_failed = True
                logger.warning(f"[{self.camera_name}] Falha ao obter frame. Reiniciando conexão...")
                self.cap.release()
                self.cap = None
                self._sleep(1.0)
                continue

            # Bloqueio ultrarrápido apenas para sobrescrever a matriz na RAM
            with self.read_lock:
                self.ret = ret
                self.frame = frame
                self.last_frame_time = time.time()
                self.frame_monotonic = time.monotonic()
                self.frame_seq += 1

            frames += 1
            elapsed = time.time() - window_start
            if elapsed >= 1.0:
                self.fps = frames / elapsed
                frames = 0
                window_start = time.time()

        if self.cap is not None:
            self.cap.release()

    def _sleep(self, seconds: float) -> None:
        """Sleep interrompível: o stop() não precisa esperar o delay inteiro."""
        end = time.time() + seconds
        while self.is_running and time.time() < end:
            time.sleep(0.1)

    @property
    def is_online(self) -> bool:
        return self.ret and (time.time() - self.last_frame_time) < self.OFFLINE_AFTER_SEC

    def read_latest(self, after_seq=None):
        """Atomic immutable snapshot; no copy and no work for a repeated frame.
        Consumers must not modify this array. Capture replaces it, never mutates it.
        """
        with self.read_lock:
            if not self.ret or self.frame is None or self.frame_seq == after_seq:
                return None
            return self.frame, self.frame_seq, self.frame_monotonic

    def read(self) -> Optional[np.ndarray]:
        """
        Retorna uma cópia do frame mais recente disponível.
        A cópia garante que a thread principal da IA possa manipular a matriz
        sem sofrer mutação por parte da thread de captura.
        """
        with self.read_lock:
            if self.frame is not None and self.ret:
                return self.frame.copy()
            return None

    def stop(self) -> None:
        """Encerra a thread graciosamente e libera os recursos de hardware."""
        self.is_running = False
        if self.thread is not None:
            self.thread.join(timeout=12.0)
        if self.cap is not None and (self.thread is None or not self.thread.is_alive()):
            self.cap.release()
        logger.info(f"[{self.camera_name}] Captura encerrada e recursos liberados.")
