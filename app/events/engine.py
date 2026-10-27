import cv2
import numpy as np
import logging
from datetime import timezone
from config.clock import datetime
from typing import List, Dict, Tuple, Any

logger = logging.getLogger(__name__)

class SpatialEventEngine:
    """
    Motor de Eventos Espaciais do RealGuard.
    Cruza dados de Tracking com Geometria de Zonas para emitir eventos atômicos.
    """
    
    def __init__(self):
        # Estado interno: track_id -> zone_id atual
        # Ajuda a disparar eventos apenas na transição (entrada/saída)
        self.active_tracks_state: Dict[int, int] = {}

    def get_anchor_point(self, bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
        """
        Calcula o ponto de ancoragem de uma pessoa.
        Em vez de usar o centroide geométrico (peito/barriga), usamos o centro-inferior (pés).
        Isso aumenta a precisão para determinar se a pessoa está "pisando" em uma zona.
        """
        x1, y1, x2, y2 = bbox
        center_x = int((x1 + x2) / 2.0)
        bottom_y = y2
        return (center_x, bottom_y)

    def is_point_in_polygon(self, point: Tuple[int, int], polygon: List[Tuple[int, int]]) -> bool:
        """
        Usa o OpenCV para verificar matematicamente se um ponto está dentro de um polígono.
        Retorna True se estiver dentro ou exatamente na borda.
        """
        if not polygon:
            return False
            
        # O OpenCV exige um array NumPy de formato específico (N, 1, 2)
        poly_array = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
        
        # pointPolygonTest retorna +1 (dentro), 0 (borda), -1 (fora)
        result = cv2.pointPolygonTest(poly_array, point, measureDist=False)
        return result >= 0

    def process_frame(
        self, 
        camera_id: int, 
        tracked_objects: Dict[int, Tuple[int, int, int, int]], 
        zones: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Compara o estado atual do rastreamento com o estado anterior.
        Emite eventos de ZONE_ENTRY e ZONE_EXIT.
        
        `zones` formato esperado: [{'id': 1, 'name': 'Restricted', 'coordinates': [(10,10), (100,10), ...]}]
        """
        events = []
        current_frame_tracks = set(tracked_objects.keys())
        now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        for track_id, bbox in tracked_objects.items():
            anchor = self.get_anchor_point(bbox)
            inside_zone_id = None

            # Descobre em qual zona a pessoa está pisando neste exato frame
            for zone in zones:
                if self.is_point_in_polygon(anchor, zone['coordinates']):
                    inside_zone_id = zone['id']
                    break  # Assume 1 zona por pessoa para simplificar o MVP

            previous_zone_id = self.active_tracks_state.get(track_id)

            # Lógica de transição de estado (Edge Triggering)
            if inside_zone_id != previous_zone_id:
                # Se estava em uma zona e agora está em outra (ou em nenhuma), dispara EXIT da antiga
                if previous_zone_id is not None:
                    events.append({
                        "event_type": "ZONE_EXIT",
                        "camera_id": camera_id,
                        "zone_id": previous_zone_id,
                        "track_id": track_id,
                        "timestamp": now_iso
                    })

                # Se entrou em uma nova zona, dispara ENTRY da nova
                if inside_zone_id is not None:
                    events.append({
                        "event_type": "ZONE_ENTRY",
                        "camera_id": camera_id,
                        "zone_id": inside_zone_id,
                        "track_id": track_id,
                        "timestamp": now_iso
                    })
                    self.active_tracks_state[track_id] = inside_zone_id
                else:
                    # Atualiza o estado removendo a zona
                    self.active_tracks_state.pop(track_id, None)

        # Limpeza (Garbage Collection): Remove tracks que sumiram da câmera
        lost_tracks = set(self.active_tracks_state.keys()) - current_frame_tracks
        for lost_id in lost_tracks:
            last_zone = self.active_tracks_state.pop(lost_id)
            events.append({
                "event_type": "ZONE_EXIT",
                "camera_id": camera_id,
                "zone_id": last_zone,
                "track_id": lost_id,
                "timestamp": now_iso
            })

        return events
