"""
Vídeo das câmeras que NÃO estão com a IA (Monitores: duas câmeras ao mesmo tempo).

A IA (detecção + reconhecimento) roda em uma câmera por vez: é o que a CPU da AIBOX aguenta sem
perder velocidade. As outras câmeras cadastradas mostram só o vídeo, sem análise:

* sob demanda: a câmera só é aberta enquanto alguém está vendo (Monitores, Ao vivo); 20 s depois
  que o último sai, a conexão é fechada e a CPU volta ao normal;
* leve: quadros reduzidos (até 1280 px de largura) a até 30 por segundo, em JPEG;
* no máximo MAX_PREVIEWS câmeras extras abertas ao mesmo tempo.

A câmera com a IA continua no /video de sempre (engine.mjpeg_stream).
"""

import os
import logging
import threading
import time
from typing import Any, Dict, Optional

from app.camera.capture import BufferlessCameraCapture

logger = logging.getLogger(__name__)

MAX_PREVIEWS = 3
IDLE_STOP_SEC = 20.0
FPS = max(1, min(60, float(os.getenv("PREVIEW_MAX_FPS", "30"))))
MAX_WIDTH = max(320, min(1920, int(os.getenv("PREVIEW_MAX_WIDTH", "1280"))))
JPEG_QUALITY = max(40, min(95, int(os.getenv("PREVIEW_JPEG_QUALITY", "85"))))


class _Worker:
    def __init__(self, cam_id: int, name: str, source: str):
        self.cam_id, self.name, self.source = cam_id, name, source
        self.capture = BufferlessCameraCapture(source, camera_name=f"Prévia {name}", reconnect_delay=5)
        self.viewers = 0
        self.last_seen = time.time()
        self.started = time.time()
        self._jpeg_lock = threading.Lock()
        self._jpeg = None
        self._encoded_at = 0.0
        self._encoded_seq = None
        self._fps_start = time.monotonic()
        self._fps_count = 0
        self.video_fps = 0.0
        self.video_size = None
        self.capture.start()

    def jpeg(self):
        """Encode once per camera/frame, share immutable bytes with all viewers."""
        import cv2
        with self._jpeg_lock:
            if not self.capture.is_online:
                return None
            now = time.monotonic()
            if self._jpeg is not None and now - self._encoded_at < 1.0 / FPS:
                return self._jpeg
            packet = self.capture.read_latest(self._encoded_seq)
            if packet is None:
                return self._jpeg
            frame, sequence, _ = packet
            h, width = frame.shape[:2]
            if width > MAX_WIDTH:
                frame = cv2.resize(frame, (MAX_WIDTH, int(h * MAX_WIDTH / width)))
            ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                self._jpeg = buffer.tobytes()
                self._encoded_at = now
                self._encoded_seq = sequence
                self.video_size = f"{frame.shape[1]}×{frame.shape[0]}"
                self._fps_count += 1
                if now-self._fps_start >= 1:
                    self.video_fps = self._fps_count/(now-self._fps_start)
                    self._fps_start, self._fps_count = now, 0
            return self._jpeg

    def stop(self) -> None:
        threading.Thread(target=self.capture.stop, daemon=True, name=f"fecha-previa-{self.cam_id}").start()


class PreviewManager:
    def __init__(self):
        self._workers: Dict[int, _Worker] = {}
        self._lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None

    # ------------------------------------------------------------ ciclo de vida
    def _ensure_reaper(self) -> None:
        if self._reaper and self._reaper.is_alive():
            return
        self._reaper = threading.Thread(target=self._reap, daemon=True, name="previas-camera")
        self._reaper.start()

    def _reap(self) -> None:
        while True:
            time.sleep(5)
            now = time.time()
            with self._lock:
                # last_seen é atualizado a cada quadro enviado: parou de atualizar = ninguém assistindo
                # (o navegador fechou a aba sem avisar, por exemplo)
                idle = [cid for cid, w in self._workers.items() if now - w.last_seen > IDLE_STOP_SEC]
                for cid in idle:
                    self._workers.pop(cid).stop()
            for cid in idle:
                logger.info("Prévia da câmera %s fechada (ninguém assistindo).", cid)

    def _get(self, cam_id: int, name: str, source: str) -> _Worker:
        with self._lock:
            w = self._workers.get(cam_id)
            if w is not None and w.source != source:      # câmera editada: reabre com a fonte nova
                self._workers.pop(cam_id).stop()
                w = None
            if w is None:
                if len(self._workers) >= MAX_PREVIEWS:
                    oldest = min(self._workers.values(), key=lambda x: (x.viewers, x.last_seen))
                    self._workers.pop(oldest.cam_id).stop()
                w = _Worker(cam_id, name, source)
                self._workers[cam_id] = w
            w.last_seen = time.time()
        self._ensure_reaper()
        return w

    def stop(self, cam_id: int) -> None:
        """Fecha a prévia (câmera virou a ativa, foi editada ou excluída)."""
        with self._lock:
            w = self._workers.pop(int(cam_id), None)
        if w:
            w.stop()

    def stop_all(self) -> None:
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        for w in workers:
            w.stop()

    # ------------------------------------------------------------ estado
    def status(self, cam_id: int) -> Dict[str, Any]:
        with self._lock:
            w = self._workers.get(int(cam_id))
        if w is None:
            return {"estado": "em_espera", "transmitindo": False}
        cap = w.capture
        frame = cap.frame if cap.is_online else None
        online = frame is not None
        out: Dict[str, Any] = {"estado": "online" if online else ("conectando" if time.time() - w.started < 15
                                                                   else "reconectando"),
                               "transmitindo": online, "fps_camera": round(cap.fps, 1) if online else None,
                               "assistindo": w.viewers}
        out["fps_video"] = round(w.video_fps, 1) if time.monotonic()-w._encoded_at < 2 else 0
        out["resolucao_video"] = w.video_size
        if online:
            out["resolucao"] = f"{frame.shape[1]}x{frame.shape[0]}"
        return out

    # ------------------------------------------------------------ vídeo
    def mjpeg(self, cam_id: int, name: str, source: str, alive=lambda: True):
        import cv2
        from app.pipeline import placeholder_jpeg
        w = self._get(int(cam_id), name, source)
        with self._lock:
            w.viewers += 1
        try:
            last_ok = time.time()
            last_jpeg = None
            while alive():
                started = time.time()
                with self._lock:
                    w.last_seen = time.time()
                    if self._workers.get(w.cam_id) is not w:      # fechada (virou ativa / editada)
                        return
                jpeg = w.jpeg()
                if jpeg is not None and jpeg is not last_jpeg:
                    last_jpeg = jpeg
                    last_ok = time.time()
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                elif jpeg is None and time.time() - last_ok > 2:
                    msg = "Conectando..." if time.time() - w.started < 15 else "Sem imagem - reconectando"
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + placeholder_jpeg(msg) + b"\r\n"
                    time.sleep(1.0)
                    continue
                time.sleep(max(0.0, 1.0 / FPS - (time.time() - started)))
        finally:
            with self._lock:
                w.viewers -= 1
                w.last_seen = time.time()


previews = PreviewManager()
