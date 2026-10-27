import os
import threading
import time
import numpy as np
import logging
from typing import Optional, Dict, Any, List, Tuple

try:
    from insightface.app import FaceAnalysis
except ImportError:  # pacote ausente: o resto do sistema continua importável
    FaceAnalysis = None

logger = logging.getLogger(__name__)

class FaceRecognizer:
    """
    Módulo de extração e comparação de biometria facial utilizando InsightFace.
    Otimizado para processamento Edge (CPU ARM64) e aderente à minimização de dados.
    """
    
    # Uma única inferência por vez: o loop de vídeo e o cadastro (API) usam
    # a mesma instância, então serializamos o acesso ao modelo.
    _lock = threading.Lock()
    _gallery_lock = threading.Lock()
    _gallery = None

    def __init__(self, model_name: str = 'buffalo_sc', threshold: float = 0.45,
                 num_threads: int = 2, det_size: int = 320):
        """
        [DECISÃO / BASELINE EXPERIMENTAL] Inicializa o modelo. 'buffalo_sc' é leve.
        O threshold inicial é 0.45 (baseline experimental, sujeito a calibração futura).
        """
        self.threshold = threshold
        self.last_quality = {}
        self._retry_size = max(320, min(640, int(os.getenv("FACE_RETRY_DET_SIZE", "640"))))
        self.last_inference_ms = 0.0
        self._gallery_lock = threading.Lock()
        self._gallery = None

        try:
            if FaceAnalysis is None:
                raise RuntimeError("Pacote 'insightface' não instalado (pip install -r requirements.txt).")
            from config.settings import resolve_insightface_root
            root = resolve_insightface_root(model_name)
            # Só detecção + reconhecimento (mesmo ajuste do EduVision): packs
            # maiores também trazem landmarks/gênero/idade, que não usamos.
            try:
                self.app = FaceAnalysis(name=model_name, root=root,
                                        allowed_modules=["detection", "recognition"],
                                        providers=['CPUExecutionProvider'])
            except TypeError:  # insightface antigo, sem allowed_modules
                self.app = FaceAnalysis(name=model_name, root=root, providers=['CPUExecutionProvider'])
            self.app.prepare(ctx_id=0, det_size=(det_size, det_size))
            self._limit_threads(num_threads)
            logger.info(f"Modelo InsightFace '{model_name}' carregado com sucesso (CPU, {num_threads} thread(s)).")
        except Exception as e:
            logger.error(f"Erro ao inicializar InsightFace: {e}")
            raise

    def _limit_threads(self, num_threads: int) -> None:
        """
        O InsightFace cria as sessões ONNX sem SessionOptions, e o ONNX Runtime
        IGNORA OMP_NUM_THREADS (usa o próprio pool, do tamanho de todos os
        núcleos). Na AIBOX isso fazia cada reconhecimento disputar os 8 núcleos
        com o YOLO e com a decodificação RTSP. Recriamos cada sessão com o
        mesmo arquivo de modelo, só limitando as threads — entradas/saídas são
        idênticas, então o resto do InsightFace segue funcionando igual.
        """
        if not num_threads or num_threads < 1:
            return
        try:
            import onnxruntime as ort
        except ImportError:
            return
        for name, model in getattr(self.app, "models", {}).items():
            model_file = getattr(model, "model_file", None)
            if not model_file or not hasattr(model, "session"):
                continue
            try:
                from app.inference_options import session_options
                opts = session_options(num_threads)
                model.session = ort.InferenceSession(model_file, sess_options=opts,
                                                     providers=['CPUExecutionProvider'])
            except Exception as e:  # nunca impede o reconhecimento de funcionar
                logger.warning(f"Não foi possível limitar threads do modelo '{name}': {e}")

    def extract_embedding(self, image_crop: np.ndarray) -> Optional[np.ndarray]:
        """
        Recebe um recorte de imagem (BBox do corpo/pessoa) e tenta extrair a face.
        Retorna apenas o vetor matemático (embedding de 512 dimensões), descartando a foto.
        """
        if image_crop is None or image_crop.size == 0:
            return None

        try:
            with self._lock:
                started = time.perf_counter()
                # Detect first and embed only the largest face, preserving the
                # previous selection rule without embedding every bystander.
                from insightface.app.common import Face
                boxes, keypoints = self.app.det_model.detect(image_crop, max_num=0, metric="default")
                retried = False
                # Retry only large native crops that lost detail at 320 pixels.
                retry_size = getattr(self, "_retry_size", 640)
                if boxes.shape[0] == 0 and max(image_crop.shape[:2]) > 320 and retry_size > 320:
                    boxes, keypoints = self.app.det_model.detect(
                        image_crop, input_size=(retry_size, retry_size), max_num=0, metric="default")
                    retried = True
                self.last_quality = {"crop_px": [image_crop.shape[1], image_crop.shape[0]],
                                     "detectado": bool(boxes.shape[0]), "segunda_passagem": retried}
                if boxes.shape[0] == 0:
                    faces = []
                else:
                    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                    index = int(np.argmax(areas))
                    face = Face(bbox=boxes[index, :4], det_score=boxes[index, 4],
                                kps=keypoints[index] if keypoints is not None else None)
                    self.last_quality.update(
                        rosto_px=[round(float(boxes[index, 2]-boxes[index, 0])),
                                  round(float(boxes[index, 3]-boxes[index, 1]))],
                        confianca=round(float(boxes[index, 4]), 3))
                    self.app.models["recognition"].get(image_crop, face)
                    faces = [face]
                self.last_inference_ms = (time.perf_counter() - started) * 1000.0
            if not faces:
                return None
                
            if len(faces) > 1:
                faces = sorted(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]), reverse=True)
                
            return faces[0].embedding
        except Exception as e:
            logger.error(f"Erro ao extrair embedding facial: {e}")
            return None

    def extract_registration_embedding(self, image: np.ndarray) -> Tuple[Optional[np.ndarray], int]:
        """
        Usado no cadastro: devolve (embedding, quantidade_de_rostos).
        Só há embedding quando a foto tem exatamente 1 rosto — com 0 não há
        o que cadastrar e com 2+ ficaria ambíguo de quem é o vetor.
        """
        if image is None or image.size == 0:
            return None, 0
        try:
            with self._lock:
                faces = self.app.get(image)
        except Exception as e:
            logger.error(f"Erro ao analisar foto de cadastro: {e}")
            return None, 0
        if len(faces) != 1:
            return None, len(faces)
        return faces[0].embedding, 1

    def compute_similarity(self, embedding_1: np.ndarray, embedding_2: np.ndarray) -> float:
        """
        Calcula a Similaridade de Cossenos entre dois embeddings faciais.
        Retorna um score em ponto flutuante tratado com clipping para [0, 1].
        """
        if embedding_1 is None or embedding_2 is None:
            return 0.0
            
        dot_product = np.dot(embedding_1, embedding_2)
        norm_1 = np.linalg.norm(embedding_1)
        norm_2 = np.linalg.norm(embedding_2)
        
        if norm_1 == 0 or norm_2 == 0:
            return 0.0
            
        similarity = dot_product / (norm_1 * norm_2)
        return float(np.clip(similarity, 0.0, 1.0))

    def identify_face(self, image_crop: np.ndarray, known_embeddings_db: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        [DECISÃO DO PROJETO] Executa o fluxo completo de reconhecimento sobre o crop de uma pessoa:
        1. Tenta extrair a face e o embedding através do InsightFace.
        2. Se não encontrar face, retorna {"type": "NO_FACE"}.
        3. Se encontrar, compara com a base externa de embeddings fornecida.
        4. Retorna {"type": "MATCH", "student_id": ..., "similarity": ...} ou {"type": "UNKNOWN", "similarity": ...}.
        """
        embedding = self.extract_embedding(image_crop)
        
        if embedding is None:
            return {"type": "NO_FACE"}
            
        if not known_embeddings_db:
            return {"type": "UNKNOWN", "similarity": 0.0}

        ids, matrix = self._normalized_gallery(known_embeddings_db, embedding.size)
        if not ids:
            return {"type": "UNKNOWN", "similarity": 0.0}
        norm = np.linalg.norm(embedding)
        if norm == 0 or not np.isfinite(norm):
            return {"type": "UNKNOWN", "similarity": 0.0}
        scores = np.clip(matrix @ (embedding / norm), 0.0, 1.0)
        index = int(np.argmax(scores))
        max_similarity = float(scores[index])
        best_match_id = ids[index] if max_similarity > 0 else None

        if max_similarity >= self.threshold and best_match_id is not None:
            return {
                "type": "MATCH",
                "student_id": best_match_id,
                "similarity": max_similarity
            }
        else:
            return {
                "type": "UNKNOWN",
                "similarity": max_similarity,
                # Melhor candidato mesmo abaixo do limiar: o IdentityManager usa
                # como evidência FRACA (ex.: rosto de lado), nunca sozinha.
                "candidate_id": best_match_id,
            }

    def _normalized_gallery(self, records, dimensions):
        # Pipeline reload replaces the list atomically. Holding the list in the
        # cache prevents id reuse; each caller gets one consistent snapshot.
        with self._gallery_lock:
            cached = self._gallery
            if cached is not None and cached[0] is records and cached[1] == dimensions:
                return cached[2], cached[3]
            ids, vectors = [], []
            for record in records:
                vector = record.get("embedding")
                if vector is None:
                    continue
                vector = np.asarray(vector, dtype=np.float32).reshape(-1)
                if vector.size != dimensions or not np.isfinite(vector).all():
                    continue
                norm = np.linalg.norm(vector)
                if norm <= 0:
                    continue
                ids.append(record.get("student_id"))
                vectors.append(vector / norm)
            matrix = np.ascontiguousarray(vectors, dtype=np.float32)
            self._gallery = (records, dimensions, ids, matrix)
            return ids, matrix

    @staticmethod
    def serialize_embedding(embedding: np.ndarray) -> bytes:
        return embedding.tobytes()

    @staticmethod
    def deserialize_embedding(blob: bytes) -> np.ndarray:
        return np.frombuffer(blob, dtype=np.float32)