import logging
import os
import queue
import threading
import time
from typing import List, Dict, Any, Optional, Tuple

import cv2
import numpy as np

from app.visualization.visualizer import Visualizer
from config.settings import DEFAULT_MODEL_PATH
from database.repository import (
    save_security_event, load_known_embeddings, load_zones, load_rules, list_students,
    auto_dismiss_security_event, save_event_snapshot,
)

from app.camera.capture import BufferlessCameraCapture
from app.detection.yolo import YOLODetector
from app.tracking.tracker import CentroidTracker
from app.recognition.insightface import FaceRecognizer
from app.recognition.crop_utils import extract_person_crop
from app.recognition.identity_manager import (
    IdentityManager, STATUS_RESOLVED, STATUS_UNKNOWN, STATUS_PENDING,
)
from app.events.engine import SpatialEventEngine
from app.rules.engine import RuleEngine

logger = logging.getLogger(__name__)


def arena_capture(frame, bbox):
    """Recorte real do track, com margem, codificado uma vez por ocorrência."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)
    dx, dy = max(8, (x2-x1)//10), max(8, (y2-y1)//10)
    crop = frame[max(0,y1-dy):min(h,y2+dy), max(0,x1-dx):min(w,x2+dx)]
    if not crop.size:
        return None
    scale = min(1.0, 960/max(crop.shape[:2]))
    if scale < 1:
        crop = cv2.resize(crop, (max(1,round(crop.shape[1]*scale)), max(1,round(crop.shape[0]*scale))), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return jpg.tobytes() if ok else None

# O rosto está na parte de cima da caixa da pessoa. Reconhecer só nessa
# faixa dá ao InsightFace um rosto proporcionalmente maior (detecta e
# compara melhor do que no corpo inteiro).
FACE_CROP_TOP_RATIO = 0.55
# Padrões (todos sobrescrevíveis pelo config/realguard.env — ver settings.py).
# Tempo mínimo entre um pedido de detecção e o próximo (YOLO_INTERVALO do
# EduVision): o YOLO roda ~6-7x/s em vez de sem pausa, deixando CPU para a
# decodificação RTSP e para o InsightFace. O vídeo continua no ritmo da câmera.
DETECTION_MIN_INTERVAL_SEC = 0.15
# "Rosto obstruído" só depois de a pessoa estar em cena por este tempo E
# de este número de tentativas sem NENHUM rosto. Antes eram só 3 tentativas
# (~4-6 s), o que marcava como obstruído quem só estava entrando/longe
# ou quando o reconhecimento atrasava.
OBSTRUCTED_MIN_SEC = 10.0
OBSTRUCTED_MIN_ATTEMPTS = 6
# Só conta como tentativa "sem rosto" para obstrução quando a pessoa ocupa
# pelo menos esta fração da altura do quadro. Pessoa pequena/longe da câmera
# não tem rosto detectável — isso é distância, não obstrução.
OBSTRUCTED_MIN_PERSON_HEIGHT = 0.25


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except ValueError:
        return default


def encode_jpeg(frame, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


def placeholder_jpeg(text: str, width: int = 640, height: int = 360) -> bytes:
    """Quadro escuro com uma mensagem (câmera offline, iniciando...)."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (24, 24, 30)
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    cv2.putText(img, text, (max(10, (width - tw) // 2), height // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else b""


class RealGuardPipeline:
    STREAM_MAX_FPS = max(1, _env_int("STREAM_MAX_FPS", 20))
    # Vídeo do dashboard: largura e qualidade do JPEG (STREAM_MAX_WIDTH / STREAM_JPEG_QUALITY no .env).
    # 640 deixava a imagem bem pior que a da câmera; 960 custa pouco mais de CPU.
    STREAM_WIDTH = max(320, _env_int("STREAM_MAX_WIDTH", 960))
    STREAM_JPEG_QUALITY = max(40, min(95, _env_int("STREAM_JPEG_QUALITY", 78)))
    VIEWER_TTL_SEC = 5.0     # sem espectador há esse tempo => nem codifica JPEG

    def __init__(
        self,
        camera_id: int,
        stream_url: str,
        zones: Optional[list] = None,
        rules: Optional[list] = None,
        model_path: str = DEFAULT_MODEL_PATH,
        known_embeddings: Optional[list] = None,
        use_face_recognition: bool = False,
        presence_service=None,
        async_recognition: bool = False,
        face_model: str = "buffalo_sc",
        face_threshold: float = 0.45,
        max_fps: float = 60.0,
        process_max_width: int = 1280,
        detection_interval_sec: float = DETECTION_MIN_INTERVAL_SEC,
        yolo_threads: int = 2,
        yolo_confidence: float = 0.3,
        face_threads: int = 2,
        recognition_interval_sec: float = 1.0,
        resolved_recheck_sec: float = 5.0,
        face_soft_threshold: float = 0.35,
        track_timeout_sec: float = 3.0,
        obstructed_min_sec: float = OBSTRUCTED_MIN_SEC,
        obstructed_min_attempts: int = OBSTRUCTED_MIN_ATTEMPTS,
        obstructed_min_person_height: float = OBSTRUCTED_MIN_PERSON_HEIGHT,
        mirror: bool = False,
    ):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.use_face_recognition = use_face_recognition
        self.presence_service = presence_service
        self.async_recognition = async_recognition
        self.max_fps = max_fps
        # Câmera de 4–8 MP: tracker, desenho, recortes e JPEG numa cópia de até esta largura
        # (o YOLO já reduz para 640 de qualquer jeito). 0 = processa na resolução original.
        self.process_max_width = max(0, int(process_max_width or 0))
        self.detection_interval_sec = max(0.0, float(detection_interval_sec))
        self.obstructed_min_sec = obstructed_min_sec
        self.obstructed_min_attempts = obstructed_min_attempts
        self.obstructed_min_person_height = obstructed_min_person_height
        # Espelhamento é só de EXIBIÇÃO: a IA, as zonas e o cadastro sempre
        # trabalham na imagem original.
        self.mirror = bool(mirror)

        self.zones = zones if zones is not None else load_zones(self.camera_id)
        self.rules = rules if rules is not None else load_rules(self.camera_id)

        if known_embeddings is not None:
            self.known_embeddings = known_embeddings
        else:
            try:
                self.known_embeddings = load_known_embeddings()
            except Exception as e:
                logger.error(f"Falha ao carregar embeddings conhecidos: {e}")
                self.known_embeddings = []
                self.use_face_recognition = False

        # student_id -> {id, name, registration_number, telegram_chat_id, ...}
        self.students: Dict[int, Dict[str, Any]] = {}

        self.camera = BufferlessCameraCapture(self.stream_url)
        self.detector = YOLODetector(model_path, conf_threshold=yolo_confidence, num_threads=yolo_threads)
        self.tracker = CentroidTracker(max_disappeared_sec=track_timeout_sec)
        self.spatial_engine = SpatialEventEngine()
        self.rule_engine = RuleEngine(self.rules)

        self.identity_manager = IdentityManager(
            recognition_interval_sec=recognition_interval_sec,
            window_size=5,
            min_evidence_count=3,
            resolved_recheck_sec=resolved_recheck_sec,
            soft_similarity=min(face_soft_threshold, face_threshold),
        )

        if self.use_face_recognition:
            self.face_recognizer = FaceRecognizer(
                model_name=face_model,
                threshold=face_threshold,
                num_threads=face_threads,
            )
        else:
            self.face_recognizer = None

        self.is_running = False
        self.visualizer = Visualizer(camera_id=str(self.camera_id))

        # --- estado compartilhado com a API (leitura por outras threads) ---
        self._id_lock = threading.RLock()     # protege identity_manager
        self._out_lock = threading.Lock()     # protege jpeg/estatísticas
        self._jpeg: Optional[bytes] = None
        self._jpeg_seq = 0
        self._last_encode = 0.0
        self._video_queue = queue.Queue(maxsize=1)
        self._video_thread = None
        self._video_count = 0
        self._video_start = time.monotonic()
        self._video_fps = 0.0
        self._video_encode_ms = 0.0
        self._video_size = None
        self._video_published_at = 0.0
        self._last_viewer = 0.0
        self._last_offline_publish = 0.0
        self._stats = {"pessoas": 0, "reconhecidos": 0, "desconhecidos": 0, "obstruidos": 0,
                       "fps_processamento": 0.0, "ultima_atualizacao": None}
        self._fps_count = 0
        self._fps_start = time.monotonic()
        self._thread: Optional[threading.Thread] = None

        # --- reconhecimento em thread própria (não trava o vídeo) ---
        self._rec_queue: "queue.Queue" = queue.PriorityQueue(maxsize=4)
        self._rec_inflight: set = set()
        self._rec_counter = 0
        self._rec_retry_after = {}
        self._rec_max_age = max(0.1, float(os.getenv("RECOGNITION_MAX_QUEUE_AGE_SEC", "1.0")))
        self._rec_min_width = max(0, _env_int("FACE_MIN_CROP_WIDTH", 0))
        self._rec_min_height = max(0, _env_int("FACE_MIN_CROP_HEIGHT", 0))
        self._rec_blur_min = max(0.0, float(os.getenv("FACE_MIN_SHARPNESS", "0")))
        self._rec_dropped = 0
        self._source_frame = None
        self._det_completed_at = 0.0
        self._det_result_age_ms = 0.0

        self._obstructed_done: set = set()
        self._obstructed_events: Dict[int, int] = {}   # track_id -> id do evento FACE_OBSTRUCTED
        self._rec_thread: Optional[threading.Thread] = None

        # --- detecção (YOLO) em thread própria (mesmo padrão do EduVision) ---
        # O loop principal (_run_loop/_process_frame) NUNCA chama
        # self.detector.detect() diretamente: ele só entrega o frame mais
        # recente para este worker e usa o último resultado já pronto.
        # Assim a captura/tracking/eventos/desenho/publicação seguem no
        # ritmo da câmera, mesmo que a inferência do YOLO seja mais lenta.
        self._det_lock = threading.Lock()
        self._det_pending_frame: Optional[np.ndarray] = None  # frame aguardando o worker
        self._det_busy = False                                # True do pedido até o resultado
        self._det_result: List[Tuple[int, int, int, int, float]] = []
        self._det_seq = 0          # incrementa a cada resultado NOVO do YOLO
        self._det_seen_seq = 0     # último resultado já entregue ao tracker
        self._det_last_request = 0.0
        self._det_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def start(self):
        """Executa o loop (bloqueante) até stop() ser chamado."""
        external_stop = getattr(self, "_external_stop", None)
        if external_stop is not None and external_stop.is_set():
            return
        from app.arena import enabled, arena
        if enabled():
            arena.bind(self.camera_id)
        self.is_running = True
        self.camera.start()
        self._video_thread = threading.Thread(target=self._video_worker, daemon=True, name="video-encoder")
        self._video_thread.start()
        self._det_thread = threading.Thread(
            target=self._detection_worker, daemon=True, name="detection")
        self._det_thread.start()
        if self.async_recognition and self.use_face_recognition:
            self._rec_thread = threading.Thread(
                target=self._recognition_worker, daemon=True, name="recognition")
            self._rec_thread.start()
        try:
            if external_stop is not None and external_stop.is_set():
                return
            self._run_loop()
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}")
        finally:
            self.stop()

    def start_background(self) -> threading.Thread:
        """Sobe o loop numa thread daemon e devolve imediatamente."""
        self._thread = threading.Thread(target=self.start, daemon=True, name="realguard-pipeline")
        self._thread.start()
        return self._thread

    def stop(self):
        self.is_running = False
        if self._video_thread and self._video_thread is not threading.current_thread():
            self._video_thread.join(timeout=3.0)
        if hasattr(self, 'camera') and self.camera:
            self.camera.stop()
        if self._det_thread and self._det_thread.is_alive() \
                and self._det_thread is not threading.current_thread():
            self._det_thread.join(timeout=3.0)
        if self._rec_thread and self._rec_thread.is_alive() \
                and self._rec_thread is not threading.current_thread():
            self._rec_thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Base de alunos (recarregável sem reiniciar)
    # ------------------------------------------------------------------
    def refresh_students(self) -> None:
        try:
            self.students = {s["id"]: s for s in list_students()}
            from app.arena import enabled
            if enabled():
                self.students = {sid:s for sid,s in self.students.items() if not s.get('ficticio')}
                self.known_embeddings = [e for e in self.known_embeddings if e.get('student_id') in self.students]
        except Exception as e:
            logger.error(f"Falha ao atualizar cache de alunos: {e}")

    def reload_known_embeddings(self) -> None:
        """
        Chamado pela API após cadastrar/editar/excluir aluno: a base nova é
        usada já no próximo reconhecimento, sem reiniciar o sistema. Os
        tracks em cena são reavaliados (quem estava 'Desconhecido' e acabou
        de ser cadastrado passa a ser reconhecido).
        """
        self.known_embeddings = load_known_embeddings()
        self.refresh_students()
        with self._id_lock:
            # Mantém quem já está reconhecido; só reavalia os demais.
            self.identity_manager.reset_for_new_base(self.students.keys())
        logger.info(f"Base facial recarregada: {len(self.known_embeddings)} embedding(s).")

    # ------------------------------------------------------------------
    # Eventos de segurança
    # ------------------------------------------------------------------
    def _dispatch_business_events(self, business_events: List[Dict[str, Any]]):
        from app.arena import enabled
        if enabled():
            return  # A Arena registra mudanças visuais na própria sessão.
        for be in business_events:
            track_id = be.get("track_id")
            if track_id is not None:
                with self._id_lock:
                    student_id = self.identity_manager.get_track_identity(self.camera_id, track_id)
                be["student_id"] = student_id
            else:
                be["student_id"] = None
            save_security_event(be)

    # ------------------------------------------------------------------
    # Detecção (YOLO) assíncrona
    # ------------------------------------------------------------------
    def _request_detection(self, frame) -> None:
        """
        Entrega o frame atual ao worker de detecção, se ele não estiver
        ocupado com o anterior. Nunca bloqueia o loop principal — se o
        worker ainda está processando, este frame é descartado (o próximo
        vem logo em seguida, ritmo da câmera).
        """
        now = time.monotonic()
        if self.detection_interval_sec and (now - self._det_last_request) < self.detection_interval_sec:
            return
        with self._det_lock:
            if self._det_busy:
                return
            self._det_busy = True
            self._det_pending_frame = frame  # immutable capture/fit buffer
        self._det_last_request = now

    def _collect_detection(self) -> Optional[List[Tuple[int, int, int, int, float]]]:
        """
        Resultado NOVO do YOLO, ou None se o worker ainda não terminou outro.
        Importante: o tracker só deve ser atualizado com detecção nova — antes
        o mesmo resultado era reaplicado a cada frame da câmera, o que fazia o
        tracker "contar sumiço" várias vezes por detecção e perder a pessoa.
        """
        with self._det_lock:
            if self._det_seq == self._det_seen_seq:
                return None
            self._det_seen_seq = self._det_seq
            return list(self._det_result)

    def _detection_worker(self):
        """
        Roda em thread própria: pega o frame mais recente entregue por
        _request_detection, roda o YOLO (a parte pesada) e publica o
        resultado. Nunca processa frame enfileirado — só o mais novo.
        """
        while self.is_running:
            with self._det_lock:
                frame = self._det_pending_frame
                self._det_pending_frame = None
            if frame is None:
                time.sleep(0.005)
                continue
            try:
                result = self.detector.detect(frame)
            except Exception:
                logger.exception("Erro no worker de detecção")
                result = []
            with self._det_lock:
                self._det_result = result
                self._det_seq += 1
                self._det_completed_at = time.monotonic()
                self._det_busy = False

    # ------------------------------------------------------------------
    # Reconhecimento facial
    # ------------------------------------------------------------------
    def _identify(self, person_crop, known_embeddings) -> Dict[str, Any]:
        if person_crop is None:
            return {"type": "NO_FACE"}
        try:
            return self.face_recognizer.identify_face(person_crop, known_embeddings)
        except Exception as e:
            logger.error(f"Erro no reconhecimento facial: {e}")
            return {"type": "NO_FACE"}

    @staticmethod
    def _head_region(bbox: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        return (x1, y1, x2, int(y1 + (y2 - y1) * FACE_CROP_TOP_RATIO))

    def _maybe_recognize(self, frame, tracked_objects, current_time):
        frame_h = frame.shape[0] if frame is not None else 0
        with self._id_lock:
            candidates = []
            for track_id, bbox in tracked_objects.items():
                result = self.identity_manager.evaluate_track(self.camera_id, track_id, current_time)
                if (result["should_recognize"] and track_id not in self._rec_inflight
                        and current_time >= self._rec_retry_after.get(track_id, 0)):
                    data = self.identity_manager.tracks_cache[(self.camera_id, track_id)]
                    candidates.append((result["status"] == STATUS_RESOLVED,
                                       data.get("last_attempt_time") or 0, track_id, bbox, data))
        for resolved, _, track_id, bbox, track_data in sorted(candidates):
            if self.async_recognition and self._rec_queue.full():
                break
            head = self._head_region(bbox)
            source = self._source_frame if self._source_frame is not None else frame
            if source is not frame:
                sx, sy = source.shape[1] / frame.shape[1], source.shape[0] / frame.shape[0]
                head = (int(head[0]*sx), int(head[1]*sy), int(head[2]*sx), int(head[3]*sy))
            crop = extract_person_crop(source, head)
            if crop is None:
                continue
            unsuitable = crop.shape[1] < self._rec_min_width or crop.shape[0] < self._rec_min_height
            if not unsuitable and self._rec_blur_min:
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                unsuitable = cv2.Laplacian(gray, cv2.CV_32F).var() < self._rec_blur_min
            if unsuitable:
                self._rec_retry_after[track_id] = current_time + .5
                continue  # poor quality is not evidence of an obstructed face
            near = frame_h > 0 and bbox[3]-bbox[1] >= self.obstructed_min_person_height*frame_h
            if self.async_recognition:
                with self._id_lock:
                    self._rec_counter += 1
                    self._rec_inflight.add(track_id)
                    try:
                        self._rec_queue.put_nowait((int(resolved), self._rec_counter, track_id,
                            crop, self.known_embeddings, near, current_time, track_data))
                    except queue.Full:
                        self._rec_inflight.discard(track_id)
            else:
                result = self._identify(crop, self.known_embeddings)
                self._record_result(track_id, result, near, current_time)

    def _record_result(self, track_id, result, near: bool, now: float) -> None:
        with self._id_lock:
            # o track pode ter sumido enquanto o modelo trabalhava
            if (self.camera_id, track_id) not in self.identity_manager.tracks_cache:
                return
            self.identity_manager.update_track(
                camera_id=self.camera_id, track_id=track_id,
                recognition_result=result, current_time=now)
            data = self.identity_manager.tracks_cache[(self.camera_id, track_id)]
            if result.get("type") == "NO_FACE":
                data.setdefault("arena_no_face_since", now)
                data["arena_no_face_count"] = data.get("arena_no_face_count", 0) + 1
            else:
                data.pop("arena_no_face_since", None)
                data["arena_no_face_count"] = 0
            if near and result.get("type") == "NO_FACE":
                data = self.identity_manager.tracks_cache[(self.camera_id, track_id)]
                data["near_no_face"] = data.get("near_no_face", 0) + 1

    def _recognition_worker(self):
        while self.is_running:
            try:
                _, _, track_id, crop, known, near, queued_at, token = self._rec_queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                with self._id_lock:
                    valid = (self.identity_manager.tracks_cache.get((self.camera_id, track_id)) is token
                             and known is self.known_embeddings
                             and time.monotonic() - queued_at <= self._rec_max_age)
                if not valid:
                    self._rec_dropped += 1
                    continue
                result = self._identify(crop, known)
                with self._id_lock:
                    if (self.identity_manager.tracks_cache.get((self.camera_id, track_id)) is token
                            and known is self.known_embeddings):
                        self._record_result(track_id, result, near, time.monotonic())
            except Exception:
                logger.exception("Falha no worker facial")
            finally:
                with self._id_lock:
                    self._rec_inflight.discard(track_id)
                self._rec_queue.task_done()

    def _is_obstructed(self, data, now: Optional[float] = None) -> bool:
        """
        Rosto obstruído = pessoa em cena há pelo menos obstructed_min_sec, com
        pelo menos obstructed_min_attempts tentativas SEM rosto feitas quando
        ela estava perto o bastante (obstructed_min_person_height) e NENHUM
        rosto encontrado em nenhuma tentativa. Quem já foi reconhecido, mostrou
        o rosto uma vez ou só passou longe da câmera nunca entra aqui.
        """
        if not data or data["status"] != STATUS_PENDING or data.get("had_face"):
            return False
        if data.get("near_no_face", 0) < self.obstructed_min_attempts:
            return False
        if any(h.get("type") != "NO_FACE" for h in data.get("history", [])):
            return False
        now = time.monotonic() if now is None else now
        return (now - data.get("created_time", now)) >= self.obstructed_min_sec

    def _trigger_obstructed(self, tracked_objects, photo_fn):
        """Rosto obstruído: grava evento de segurança (validação na Portaria) e avisa segurança/gerência."""
        now = time.monotonic()
        with self._id_lock:
            cache = self.identity_manager.tracks_cache
            ids = [t for t in tracked_objects
                   if self._is_obstructed(cache.get((self.camera_id, t)), now)]
            # O rosto apareceu depois do alerta? Então não era obstrução:
            # o evento sai da fila da Portaria automaticamente.
            revealed = [t for t, _ in self._obstructed_events.items()
                        if (cache.get((self.camera_id, t)) or {}).get("had_face")]
        for track_id in revealed:
            event_id = self._obstructed_events.pop(track_id, None)
            if event_id is not None:
                auto_dismiss_security_event(event_id, "rosto identificado depois do alerta")
                from app.security_alerts import request_panel_update
                request_panel_update()
        for track_id in ids:
            if track_id in self._obstructed_done:
                continue
            self._obstructed_done.add(track_id)
            event_id = save_security_event({
                "event_type": "FACE_OBSTRUCTED", "camera_id": self.camera_id, "track_id": track_id,
                "severity": "ORANGE", "description": "Pessoa com rosto obstruído/não visível detectada.",
            }, return_id=True)
            if event_id:
                self._obstructed_events[track_id] = event_id
                if photo_fn is not None:
                    save_event_snapshot(event_id, photo_fn)
                from app.security_alerts import request_panel_update
                request_panel_update()          # painel fixo no Telegram (mesmo durante o cooldown do aviso)
            if self.presence_service:
                self.presence_service.notify_security(track_id, photo_fn, event_id=event_id)

    def _build_identities(self, tracked_objects) -> Dict[int, Dict[str, str]]:
        """Rótulo/cor de cada pessoa em cena, para o desenho e para os contadores."""
        identities: Dict[int, Dict[str, str]] = {}
        now = time.monotonic()
        with self._id_lock:
            for track_id in tracked_objects:
                data = self.identity_manager.tracks_cache.get((self.camera_id, track_id))
                status = data["status"] if data else None
                if status == STATUS_RESOLVED and data["student_id"] is not None:
                    student = self.students.get(data["student_id"], {})
                    name = student.get("name") or f"ID {data['student_id']}"
                    identities[track_id] = {
                        "state": "recognized",
                        "student_id": data["student_id"],
                        "label": f"{name}  {data['similarity'] * 100:.0f}%",
                        "name": name,
                        "class_name": student.get("class_name") or "",
                        "similarity": round(float(data["similarity"]), 3),
                    }
                elif self._is_obstructed(data, now):
                    identities[track_id] = {"state": "obstructed", "label": "Rosto obstruido", "name": "Rosto obstruído"}
                elif status == STATUS_UNKNOWN:
                    identities[track_id] = {"state": "unknown", "label": "Nao cadastrado", "name": "Não cadastrado"}
                else:
                    identities[track_id] = {"state": "pending", "label": "Identificando...", "name": "Identificando…"}
        return identities

    def _trigger_presences(self, tracked_objects, photo_fn=None):
        """
        Gatilho de presença: track com identidade estabilizada => registra
        presença (uma por aparição) e dispara o Telegram (ver app/presence.py).
        """
        if not self.presence_service:
            return
        with self._id_lock:
            resolved = []
            for track_id in tracked_objects:
                data = self.identity_manager.tracks_cache.get((self.camera_id, track_id))
                if data and data["status"] == STATUS_RESOLVED and data["student_id"] is not None:
                    resolved.append((track_id, data["student_id"], data["similarity"]))
        for track_id, student_id, similarity in resolved:
            student = self.students.get(student_id)
            if student:
                self.presence_service.handle_resolved(student, track_id, similarity, photo_fn=photo_fn)

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------
    def _process_frame(self, frame) -> bool:
        """Processa um frame. Retorna False se a visualização local pediu para sair."""
        self._request_detection(frame)          # entrega ao worker e segue (não bloqueia)
        detections = self._collect_detection()  # None = YOLO ainda não terminou o próximo
        if detections is not None:
            tracked_objects = self.tracker.update([d[:4] for d in detections])
        else:
            tracked_objects = self.tracker.current()

        current_time = time.monotonic()
        if self.use_face_recognition and self.face_recognizer:
            self._maybe_recognize(frame, tracked_objects, current_time)

        # Identidade é descartada só quando o TRACK morre (sumiu por mais de
        # TRACK_TIMEOUT_SEC), não quando some da tela por instantes.
        alive = getattr(self.tracker, "alive_ids", None)
        active_track_ids = alive() if callable(alive) else set(tracked_objects.keys())
        if not isinstance(active_track_ids, set):
            active_track_ids = set(tracked_objects.keys())
        with self._id_lock:
            cached_keys = list(self.identity_manager.tracks_cache.keys())
            for cam_id, trk_id in cached_keys:
                if cam_id == self.camera_id and trk_id not in active_track_ids:
                    self.identity_manager.remove_track(cam_id, trk_id)
                    self._obstructed_done.discard(trk_id)
                    self._rec_retry_after.pop(trk_id, None)
                    self._obstructed_events.pop(trk_id, None)
                    if self.presence_service:
                        self.presence_service.forget_track(trk_id)

        spatial_events = self.spatial_engine.process_frame(self.camera_id, tracked_objects, self.zones)
        business_events = self.rule_engine.process(spatial_events)
        self._dispatch_business_events(business_events)

        identities = self._build_identities(tracked_objects) if self.use_face_recognition else {}
        from app.arena import enabled as arena_enabled, arena
        if arena_enabled():
            with self._id_lock:
                for tid, identity in identities.items():
                    data = self.identity_manager.tracks_cache.get((self.camera_id, tid), {})
                    obstructed = (data.get("arena_no_face_count", 0) >= 3 and
                                  current_time-data.get("arena_no_face_since", current_time) >= 3)
                    identity["obstructed"] = obstructed or identity.get("state") == "obstructed"
                    if identity["obstructed"]:
                        identity["label"] = (identity.get("name", "") + " · Rosto obstruido")
                        identity["state"] = "obstructed"
            if detections is not None:
                def arena_photo(track_id):
                    return arena_capture(frame, tracked_objects[track_id])
                arena.observe(self.camera_id, {tid:identities.get(tid, {"state":"pending", "name":"Identificando…"})
                    for tid in tracked_objects if self.tracker.misses.get(tid, 0) == 0}, photo_fn=arena_photo)

        # Drawing is as lazy as JPEG encoding. Keep the source immutable so a
        # notification worker can render evidence after this iteration ends.
        frame_to_show = None
        render_due = (self.visualizer.show_visualization or
                      (self._viewer_active() and time.time()-self._last_encode >= 1.0/self.STREAM_MAX_FPS))
        if render_due:
            frame_to_show = self.visualizer.draw(
                frame=frame, tracked_objects=tracked_objects, zones=self.zones,
                events=business_events, identities=identities or None, mirror=self.mirror)
        else:
            self.visualizer._update_alerts(business_events)

        if self.use_face_recognition:
            def photo_fn(img=frame, tracked=dict(tracked_objects), ids=identities, zones=self.zones):
                return encode_jpeg(self.visualizer.render(img, tracked, zones, ids or None, mirror=False))
            if not arena_enabled():
                self._trigger_presences(tracked_objects, photo_fn)
                self._trigger_obstructed(tracked_objects, photo_fn)

        self._update_stats(tracked_objects, identities)
        if frame_to_show is not None:
            self._publish_frame(frame_to_show)
            return self.visualizer.show(frame_to_show)
        return True

    def _fit(self, frame):
        """Reduz quadros grandes (câmera de muitos megapixels) antes de todo o resto do processamento."""
        width = self.process_max_width
        if not width or frame is None or frame.shape[1] <= width:
            return frame
        h, w = frame.shape[:2]
        return cv2.resize(frame, (width, int(round(h * width / w))), interpolation=cv2.INTER_LINEAR)

    def _run_loop(self):
        # min_interval é só um teto de segurança (evita o loop girar mais
        # rápido que qualquer câmera real entrega, caso a leitura vire cache
        # puro). Com detecção (YOLO) e reconhecimento (InsightFace) já
        # assíncronos, o que sobra aqui por iteração é barato (tracker,
        # zonas, regras, desenho, publish), então na prática quem manda no
        # ritmo é a câmera (via _camera.read()/last_frame_time), não este
        # sleep — desde que max_fps não fique artificialmente baixo (era
        # 15.0 fixo antes; ver config/settings.py MAX_FPS).
        min_interval = 1.0 / self.max_fps if self.max_fps and self.max_fps > 0 else 0.0
        last_frame_time = None
        last_seq = None
        errors = 0

        while self.is_running:
            started = time.monotonic()
            reader = getattr(self.camera, "read_latest", None)
            if callable(reader) and isinstance(getattr(self.camera, "frame_seq", None), int):
                packet = reader(last_seq)
                if packet is None or not self.camera.is_online:
                    if not self.camera.is_online:
                        self._publish_offline()
                    time.sleep(.01)
                    continue
                frame, last_seq, captured_at = packet
            else:  # compatibility with third-party camera adapters
                frame = self.camera.read()
                stamp = getattr(self.camera, "last_frame_time", None)
                if frame is None or (isinstance(stamp, float) and stamp == last_frame_time):
                    time.sleep(.01)
                    continue
                last_frame_time = stamp
            self._source_frame = frame
            frame = self._fit(frame)

            try:
                if not self._process_frame(frame):
                    self.is_running = False
                    break
            except Exception:
                # Um frame ruim nunca derruba o sistema: registra e segue.
                errors += 1
                if errors <= 3 or errors % 100 == 0:
                    logger.exception(f"Erro ao processar frame (ocorrência #{errors})")
                time.sleep(0.05)
                continue

            self._source_frame = None
            spent = time.monotonic() - started
            if spent < min_interval:
                time.sleep(min_interval - spent)

    # ------------------------------------------------------------------
    # Saídas para a API (dashboard)
    # ------------------------------------------------------------------
    def _update_stats(self, tracked_objects, identities):
        self._fps_count += 1
        now = time.monotonic()
        elapsed = now - self._fps_start
        with self._out_lock:
            if elapsed >= 1.0:
                self._stats["fps_processamento"] = round(self._fps_count / elapsed, 1)
                self._fps_count = 0
                self._fps_start = now
            self._stats["pessoas"] = len(tracked_objects)
            self._stats["reconhecidos"] = sum(1 for i in identities.values() if i["state"] == "recognized")
            self._stats["desconhecidos"] = sum(1 for i in identities.values() if i["state"] == "unknown")
            self._stats["obstruidos"] = sum(1 for i in identities.values() if i["state"] == "obstructed")
            self._stats["ultima_atualizacao"] = time.strftime("%H:%M:%S")

    def _viewer_active(self) -> bool:
        return (time.time() - self._last_viewer) < self.VIEWER_TTL_SEC

    def _set_jpeg(self, data: bytes) -> None:
        with self._out_lock:
            self._jpeg = data
            self._jpeg_seq += 1

    def _publish_frame(self, frame) -> None:
        """Codifica o quadro anotado em JPEG — só se alguém está assistindo."""
        if frame is None or not self._viewer_active():
            return
        now = time.time()
        if now - self._last_encode < 1.0 / self.STREAM_MAX_FPS:
            return
        self._last_encode = now
        # One pending frame: a slow encoder never accumulates old video.
        try:
            self._video_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._video_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._video_queue.put_nowait(frame)
            except queue.Full:
                pass

    def _video_worker(self):
        while self.is_running:
            try:
                frame = self._video_queue.get(timeout=.2)
            except queue.Empty:
                continue
            if not self._viewer_active():
                continue
            try:
                started = time.monotonic()
                h, w = frame.shape[:2]
                if w > self.STREAM_WIDTH:
                    frame = cv2.resize(frame, (self.STREAM_WIDTH, round(h*self.STREAM_WIDTH/w)))
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.STREAM_JPEG_QUALITY])
                if ok:
                    self._video_encode_ms = (time.monotonic()-started)*1000
                    self._video_size = f"{frame.shape[1]}×{frame.shape[0]}"
                    self._video_count += 1
                    now = time.monotonic()
                    if now-self._video_start >= 1:
                        self._video_fps = self._video_count/(now-self._video_start)
                        self._video_start, self._video_count = now, 0
                    self._video_published_at = now
                    self._set_jpeg(buf.tobytes())
            except Exception:
                logger.exception("Falha ao codificar vídeo")

    def _publish_offline(self) -> None:
        now = time.time()
        if now - self._last_offline_publish < 1.0:
            return
        self._last_offline_publish = now
        if self._viewer_active():
            self._set_jpeg(placeholder_jpeg("Camera offline - reconectando..."))

    def mjpeg_stream(self):
        """Generator consumido pelo endpoint /video."""
        last_seq = -1
        while self.is_running:
            self._last_viewer = time.time()
            with self._out_lock:
                jpeg, seq = self._jpeg, self._jpeg_seq
            if jpeg is None or seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = seq
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")

    def snapshot(self) -> Optional[np.ndarray]:
        """Frame atual da câmera (sem anotações), para cadastrar rosto pela câmera do sistema."""
        return self.camera.read()

    def get_status(self) -> Dict[str, Any]:
        with self._out_lock:
            stats = dict(self._stats)
        return {
            "camera_online": bool(getattr(self.camera, "is_online", False)),
            "fps_camera": round(float(getattr(self.camera, "fps", 0.0)), 1),
            "reconhecimento_facial": bool(self.use_face_recognition and self.face_recognizer),
            "rostos_na_base": len(self.known_embeddings),
            "espelhado": self.mirror,
            # Custo real de cada modelo no hardware (compare PC x AIBOX).
            "ms_deteccao": round(float(getattr(self.detector, "last_inference_ms", 0.0) or 0.0), 1),
            "ms_reconhecimento": round(float(getattr(self.face_recognizer, "last_inference_ms", 0.0) or 0.0), 1)
            if self.face_recognizer else None,
            "fila_reconhecimento": self._rec_queue.qsize(),
            "reconhecimentos_descartados": self._rec_dropped,
            "captura_backend": getattr(self.camera, "backend_name", "opencv"),
            "yolo_entrada": [getattr(self.detector, "input_width", None), getattr(self.detector, "input_height", None)],
            "idade_resultado_deteccao_ms": round((time.monotonic()-self._det_completed_at)*1000, 1)
                if self._det_completed_at else None,
            # Painel de monitores: tamanho do quadro e há quanto tempo chegou o último.
            "fps_video": round(self._video_fps, 1) if time.monotonic()-self._video_published_at < 2 else 0,
            "resolucao_video": self._video_size,
            "ms_video_encode": round(self._video_encode_ms, 1),
            "limite_fps_video": self.STREAM_MAX_FPS,
            "qualidade_rosto": dict(getattr(self.face_recognizer, "last_quality", {}) or {}),
            "resolucao": self._frame_size(),
            "ultimo_quadro_s": round(time.time() - float(getattr(self.camera, "last_frame_time", 0.0) or 0.0), 1)
            if getattr(self.camera, "last_frame_time", 0.0) else None,
            **stats,
        }

    def _frame_size(self) -> Optional[str]:
        frame = getattr(self.camera, "frame", None)
        shape = getattr(frame, "shape", None)
        return f"{shape[1]}×{shape[0]}" if shape is not None and len(shape) >= 2 else None
