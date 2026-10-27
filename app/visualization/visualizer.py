# app/visualization/visualizer.py
import cv2
import os
import time
import numpy as np
from typing import Dict, Tuple, List, Optional, Any

class Visualizer:
    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.show_visualization = os.environ.get("SHOW_VISUALIZATION", "false").lower() == "true"
        self.window_name = f"RealGuard - CAM {self.camera_id}"
        
        self.last_time = time.monotonic()
        self.recent_alerts = []
        self.alert_display_seconds = 5.0

    def _get_fps(self) -> int:
        current_time = time.monotonic()
        diff = current_time - self.last_time
        fps = 1.0 / diff if diff > 0 else 0.0
        self.last_time = current_time
        return int(fps)

    def draw(self, frame: np.ndarray, tracked_objects: Dict[int, Tuple[int, int, int, int]], zones: List[Dict[str, Any]], events: List[Dict[str, Any]], identities: Optional[Dict[int, Dict[str, str]]] = None, mirror: bool = False) -> Optional[np.ndarray]:
        """
        Gera uma cópia do frame com os dados sobrepostos (chamado 1x por frame).
        - tracked_objects: { track_id: (x1, y1, x2, y2) }
        - zones: [{"id": 1, "name": "Restrita", "coordinates": [(x,y), ...]}, ...]
        - events: [{"event_type": "TIPO", "zone_id": 1, "track_id": 1, "student_id": 42}, ...]
        - mirror: espelha a imagem na horizontal (visão "de espelho", como uma
          webcam). Caixas e zonas são reposicionadas e os TEXTOS continuam
          legíveis, porque são desenhados depois do espelhamento.
        """
        if frame is None:
            return None
        self._fps = self._get_fps()
        self._update_alerts(events)
        return self.render(frame, tracked_objects, zones, identities, mirror)

    def render(self, frame: np.ndarray, tracked_objects: Dict[int, Tuple[int, int, int, int]], zones: List[Dict[str, Any]], identities: Optional[Dict[int, Dict[str, str]]] = None, mirror: bool = False) -> Optional[np.ndarray]:
        """Desenha sem alterar estado (usado também para a foto do Telegram)."""
        if frame is None:
            return None
        # cv2.flip já devolve uma cópia nova: espelhar custa o mesmo que o
        # frame.copy() que era feito antes — nenhum custo extra por frame.
        out_frame = cv2.flip(frame, 1) if mirror else frame.copy()
        width = frame.shape[1]
        if mirror:
            tracked_objects = {tid: self._mirror_box(b, width) for tid, b in (tracked_objects or {}).items()}
            zones = [dict(z, coordinates=[(width - 1 - int(x), int(y)) for x, y in z.get("coordinates", [])])
                     for z in (zones or [])]
        person_count = len(tracked_objects) if tracked_objects else 0

        self._draw_zones(out_frame, zones)
        self._draw_objects(out_frame, tracked_objects, identities)
        # Sem cabeçalho (câmera/pessoas/FPS) nem texto de alertas na imagem:
        # essas informações já estão nos cards do dashboard. Na imagem ficam
        # só as caixas com o nome/estado de cada pessoa.
        if self.show_visualization:   # janela local de depuração (SHOW_VISUALIZATION=true)
            self._draw_overlay(out_frame, person_count, getattr(self, "_fps", 0))

        return out_frame

    @staticmethod
    def _mirror_box(bbox, width: int):
        x1, y1, x2, y2 = map(int, bbox[:4])
        return (width - 1 - x2, y1, width - 1 - x1, y2)

    def _update_alerts(self, events: List[Dict[str, Any]]):
        current_time = time.monotonic()
        
        if events:
            for event in events:
                event_type = event.get("event_type", "UNKNOWN_EVENT")
                zone_id = event.get("zone_id", "")
                track_id = event.get("track_id", "")
                student_id = event.get("student_id", "")
                
                msg = f"ALERTA: {event_type}"
                if zone_id: msg += f" | ZONA: {zone_id}"
                if track_id: msg += f" | TRK: {track_id}"
                if student_id: msg += f" | STD: {student_id}"
                
                self.recent_alerts.append({
                    "msg": msg,
                    "expires": current_time + self.alert_display_seconds
                })
        
        self.recent_alerts = [a for a in self.recent_alerts if a["expires"] > current_time]

    def _draw_zones(self, frame: np.ndarray, zones: List[Dict[str, Any]]):
        if not zones:
            return
            
        for zone in zones:
            coordinates = zone.get("coordinates", [])
            name = zone.get("name", "ZONA")
            
            if coordinates:
                pts = np.array(coordinates, np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], isClosed=True, color=(0, 165, 255), thickness=2)
                
                x, y = pts[0][0]
                cv2.putText(frame, name, (x, max(0, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

    # Cores BGR por estado — as mesmas do dashboard (verde/vermelho/âmbar/cinza).
    COLORS = {
        "recognized": (74, 176, 34),     # verde    — aluno reconhecido
        "unknown": (77, 72, 229),        # vermelho — rosto visto, sem cadastro
        "obstructed": (25, 178, 250),    # âmbar    — rosto não visível
        "pending": (160, 150, 140),      # cinza    — ainda avaliando
    }
    TEXT_ON = {"obstructed": (20, 20, 20), "pending": (20, 20, 20)}   # texto escuro em fundo claro
    FONT = cv2.FONT_HERSHEY_SIMPLEX

    @staticmethod
    def _ascii(text: str) -> str:
        """O OpenCV só desenha ASCII: 'João' viraria 'Jo??o'. Remove os acentos."""
        import unicodedata
        return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")

    @staticmethod
    def _k(frame) -> float:
        """
        Fator de tamanho do desenho. O vídeo vai para o navegador com ~640–960 px de largura:
        numa câmera de muitos megapixels, caixa e nome com tamanho fixo em pixels ficavam
        minúsculos e ilegíveis. Tudo é desenhado proporcional à largura do quadro.
        """
        return max(1.0, frame.shape[1] / 760.0)

    def _draw_box(self, frame, x1, y1, x2, y2, color):
        """Contorno fino + cantos reforçados (estilo câmera de segurança)."""
        k = self._k(frame)
        w, h = x2 - x1, y2 - y1
        c = int(max(8 * k, min(22 * k, min(w, h) * 0.18)))
        thin, thick = max(1, int(round(1.4 * k))), max(3, int(round(3 * k)))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thin, cv2.LINE_AA)
        for (px, py, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(frame, (px, py), (px + dx * c, py), color, thick, cv2.LINE_AA)
            cv2.line(frame, (px, py), (px, py + dy * c), color, thick, cv2.LINE_AA)

    def _draw_label(self, frame, x, y_top, y_bottom, text, color, text_color):
        """Etiqueta preenchida acima da caixa (ou dentro, se não couber no topo)."""
        k = self._k(frame)
        scale = 0.5 * k
        weight = max(1, int(round(1.2 * k)))
        (tw, th), base = cv2.getTextSize(text, self.FONT, scale, weight)
        pad_x, pad_y = int(7 * k), int(5 * k)
        h = th + base + pad_y * 2
        y0 = y_top - h - 2 if y_top - h - 2 >= 0 else y_top + 2
        x0 = max(0, min(x, frame.shape[1] - tw - pad_x * 2))
        cv2.rectangle(frame, (x0, y0), (x0 + tw + pad_x * 2, y0 + h), color, -1)
        cv2.putText(frame, text, (x0 + pad_x, y0 + pad_y + th), self.FONT, scale, text_color, weight, cv2.LINE_AA)

    def _draw_objects(self, frame: np.ndarray, tracked_objects: Dict[int, Tuple[int, int, int, int]], identities: Optional[Dict[int, Dict[str, str]]] = None):
        if not tracked_objects:
            return
        for track_id, bbox in tracked_objects.items():
            if len(bbox) < 4:
                continue
            x1, y1, x2, y2 = map(int, bbox[:4])
            info = (identities or {}).get(track_id)
            state = info.get("state", "pending") if info else "pending"
            color = self.COLORS.get(state, self.COLORS["pending"])
            label = self._ascii(info.get("label", "Pessoa")) if info else "Pessoa"
            self._draw_box(frame, x1, y1, x2, y2, color)
            # Quem ainda está sendo identificado fica só com a caixa cinza: etiqueta
            # "Identificando..." em todo mundo polui o vídeo em cena cheia.
            if state != "pending":
                self._draw_label(frame, x1, y1, y2, label, color, self.TEXT_ON.get(state, (255, 255, 255)))

    def _draw_overlay(self, frame: np.ndarray, person_count: int, fps: int):
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 40), (0, 0, 0), -1)
        header_text = f"REALGUARD | CAM: {self.camera_id} | Pessoas: {person_count} | FPS: {fps}"
        cv2.putText(frame, header_text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        if self.recent_alerts:
            y_offset = frame.shape[0] - 20
            for alert in reversed(self.recent_alerts):
                cv2.putText(frame, alert["msg"], (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                y_offset -= 30

    def show(self, frame: np.ndarray) -> bool:
        """
        Retorna True para continuar a execução.
        Retorna False caso a visualização seja encerrada manualmente (Q/q/ESC).
        """
        if not self.show_visualization:
            return True 
        
        if frame is not None:
            cv2.imshow(self.window_name, frame)
            
            key = cv2.waitKey(1) & 0xFF
            if key in [ord('q'), ord('Q'), 27]:
                cv2.destroyWindow(self.window_name)
                return False 
                
        return True