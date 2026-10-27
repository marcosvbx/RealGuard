import numpy as np
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

def extract_person_crop(frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
    """
    [DECISÃO DO PROJETO] Transforma um frame BGR e um BBox do corpo (fornecido pelo YOLO/Tracker)
    em um recorte espacial isolado da pessoa.
    
    O BBox representa o corpo/pessoa, e não uma face.
    Trata coordenadas inválidas, limites ultrapassados (clipping) e evita índices negativos.
    """
    if frame is None or frame.size == 0 or not bbox:
        return None

    try:
        frame_h, frame_w = frame.shape[:2]
        x1, y1, x2, y2 = map(int, bbox)

        x_min, x_max = min(x1, x2), max(x1, x2)
        y_min, y_max = min(y1, y2), max(y1, y2)

        if x_max <= 0 or y_max <= 0 or x_min >= frame_w or y_min >= frame_h:
            return None

        clamped_x1 = max(0, x_min)
        clamped_y1 = max(0, y_min)
        clamped_x2 = min(frame_w, x_max)
        clamped_y2 = min(frame_h, y_max)

        if clamped_x2 <= clamped_x1 or clamped_y2 <= clamped_y1:
            return None

        crop = frame[clamped_y1:clamped_y2, clamped_x1:clamped_x2].copy()
        
        if crop.size == 0:
            return None

        return crop

    except Exception as e:
        logger.error(f"Erro ao extrair crop da pessoa do BBox {bbox}: {e}")
        return None