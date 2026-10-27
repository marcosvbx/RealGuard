import cv2
import numpy as np
import onnxruntime as ort
import logging
import time
from typing import List, Tuple

logger = logging.getLogger(__name__)

class YOLODetector:
    """
    Detector de objetos baseado em YOLOv8 (formato ONNX).
    Otimizado para inferência Edge no AIBOX (CPU ARM64).
    """
    
    def __init__(self, model_path: str, conf_threshold: float = 0.3, iou_threshold: float = 0.45,
                 num_threads: int = 2):
        self.model_path = model_path
        # 0.3 = mesmo valor do EduVision. Com 0.5 o YOLO "perdia" a pessoa com
        # mais frequência (pose de lado, parcialmente coberta), o que fazia o
        # track cair e o reconhecimento recomeçar.
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.last_inference_ms = 0.0
        
        # Inicia a sessão ONNX focada em CPU (seguro para o AIBOX).
        # intra_op_num_threads limita quantos núcleos UMA inferência usa —
        # mesmo ajuste do OMP_NUM_THREADS/OPENBLAS_NUM_THREADS do EduVision,
        # só que direto na sessão (não depende de variável de ambiente ter
        # sido setada antes deste import). Sem isso, o YOLO tende a tomar
        # todos os núcleos da AIBOX, brigando com a thread de captura RTSP
        # e a de reconhecimento facial (InsightFace), que também competem
        # pela CPU ARM de 8 núcleos.
        try:
            from app.inference_options import session_options
            sess_options = session_options(num_threads)
            self.session = ort.InferenceSession(
                self.model_path, sess_options=sess_options, providers=['CPUExecutionProvider']
            )
            self.input_name = self.session.get_inputs()[0].name
            self.input_shape = self.session.get_inputs()[0].shape # Geralmente [1, 3, 640, 640]
            self.input_width = self.input_shape[3]
            self.input_height = self.input_shape[2]
            logger.info(f"Modelo YOLO carregado com sucesso de {model_path}")
        except Exception as e:
            logger.error(f"Erro ao carregar modelo YOLO ONNX: {e}")
            raise

    def detect(self, frame: np.ndarray) -> List[Tuple[int, int, int, int, float]]:
        """
        Recebe um frame BGR, processa no YOLO e retorna Bounding Boxes de pessoas.
        Retorno: Lista de tuplas (x1, y1, x2, y2, confidence).
        """
        original_height, original_width = frame.shape[:2]

        # 1. Pré-processamento: Redimensionar e converter para formato do modelo
        blob = cv2.dnn.blobFromImage(frame, 1/255.0, (self.input_width, self.input_height), swapRB=True, crop=False)
        
        # 2. Inferência (ONNX) — tempo medido para o /api/status
        started = time.perf_counter()
        outputs = self.session.run(None, {self.input_name: blob})[0]
        self.last_inference_ms = (time.perf_counter() - started) * 1000.0
        
        # YOLOv8 ONNX output shape é [1, 84, 8400] (84 = 4 bbox coords + 80 classes)
        outputs = np.squeeze(outputs).T # Muda para [8400, 84]
        
        # Fatores de escala para restaurar o BBox ao tamanho original da imagem
        x_factor = original_width / self.input_width
        y_factor = original_height / self.input_height

        # 3. Pós-processamento vetorizado (a classe 0 do COCO é 'person' = coluna 4).
        # Evita um laço Python sobre as 8400 linhas a cada frame — pesado em ARM.
        scores = outputs[:, 4]
        keep = scores >= self.conf_threshold
        selected = outputs[keep]

        boxes = []
        confidences = []
        for row in selected:
            cx, cy, w, h = row[0], row[1], row[2], row[3]
            boxes.append([
                int((cx - w / 2) * x_factor),
                int((cy - h / 2) * y_factor),
                int(w * x_factor),
                int(h * y_factor),
            ])
            confidences.append(float(row[4]))

        # 4. Non-Maximum Suppression (NMS) para remover caixas duplicadas na mesma pessoa
        # ATENÇÃO: iou_threshold DEVE estar no intervalo [0, 1]. Um valor fora dessa faixa
        # (ex: 45) faz o NMS nunca suprimir nada, pois nenhum IoU real ultrapassa 1.0 —
        # isso gerava dezenas de caixas duplicadas para a mesma pessoa (bug das "43 pessoas").
        indices = cv2.dnn.NMSBoxes(boxes, confidences, self.conf_threshold, self.iou_threshold)
        
        final_detections = []
        if len(indices) > 0:
            for i in indices.flatten():
                x, y, w, h = boxes[i]
                # Converte de (x, y, w, h) para (x1, y1, x2, y2)
                final_detections.append((x, y, x + w, y + h, confidences[i]))
                
        return final_detections