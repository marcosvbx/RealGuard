import time
import numpy as np
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple


class CentroidTracker:
    """
    Rastreador leve baseado em distância entre centroides (sem dependências
    pesadas, barato em CPU ARM).

    Duas decisões importantes para a identidade "segurar" na pessoa:

    1. O sumiço é medido em SEGUNDOS, não em frames. Antes era
       "max_disappeared=15 frames": como o loop principal reaplicava o último
       resultado do YOLO a cada frame da câmera, uma falha de ~0,6 s do YOLO
       (alguém passando na frente, pose ruim) já apagava o track — a pessoa
       ganhava um ID novo e o reconhecimento recomeçava do zero. E quanto mais
       FPS, menor ficava essa janela. Agora a tolerância é fixa em tempo
       (padrão 3 s, a mesma do EduVision), independente do FPS.

    2. update() deve ser chamado só quando chega uma detecção NOVA do YOLO
       (ver RealGuardPipeline._process_frame). Entre uma detecção e outra, o
       pipeline usa current() — o último estado conhecido.

    3. "Vivo" x "visível": um track sumido continua VIVO (mantém ID e,
       no pipeline, a identidade) por max_disappeared_sec, mas só é VISÍVEL
       — desenhado, contado e enviado ao reconhecimento — se foi visto há no
       máximo visible_grace_sec. Assim não ficam caixas "fantasma" onde a
       pessoa já não está, e o InsightFace não gasta CPU com recorte vazio.

    4. Uma ou duas detecções perdidas não apagam a caixa: o YOLO às vezes
       "pisca" (pose, desfoque) e, na AIBOX, cada detecção leva 0,3–0,5 s —
       uma única falha já passava dos 0,5 s e a caixa sumia e voltava na
       tela. Agora o track segue visível até misses_to_hide detecções
       seguidas sem ele, desde que visto há no máximo visible_hold_sec.
    """

    def __init__(
        self,
        max_disappeared_sec: float = 3.0,
        max_distance: int = 150,
        clock: Callable[[], float] = time.monotonic,
        visible_grace_sec: float = 0.5,
        misses_to_hide: int = 3,
        visible_hold_sec: float = 1.2,
    ):
        # max_disappeared_sec: por quanto tempo um ID sobrevive sem ser visto
        # max_distance: distância mínima garantida (px) para associar centroides;
        #   pessoas grandes/perto da câmera ganham um raio proporcional à caixa.
        self.next_object_id = 0
        self.objects: "OrderedDict[int, Tuple[Tuple[int, int], Tuple[int, int, int, int]]]" = OrderedDict()
        self.last_seen: "OrderedDict[int, float]" = OrderedDict()
        self.max_disappeared_sec = max_disappeared_sec
        self.max_distance = max_distance
        self.visible_grace_sec = visible_grace_sec
        self.misses_to_hide = max(1, int(misses_to_hide))
        self.visible_hold_sec = max(visible_grace_sec, visible_hold_sec)
        self.misses: Dict[int, int] = {}     # detecções seguidas sem este track
        self._last_update: Optional[float] = None
        self.update_interval = 0.0           # tempo médio entre detecções do YOLO (s)
        self._intervals = 0
        self._clock = clock

    # ------------------------------------------------------------------
    def register(self, centroid: Tuple[int, int], bbox: Tuple[int, int, int, int], now: Optional[float] = None):
        """Registra um novo ID no sistema."""
        self.objects[self.next_object_id] = (centroid, bbox)
        self.last_seen[self.next_object_id] = self._clock() if now is None else now
        self.misses[self.next_object_id] = 0
        self.next_object_id += 1

    def deregister(self, object_id: int):
        """Remove um ID do sistema."""
        self.objects.pop(object_id, None)
        self.last_seen.pop(object_id, None)
        self.misses.pop(object_id, None)

    def current(self) -> Dict[int, Tuple[int, int, int, int]]:
        """Tracks VISÍVEIS agora (ID -> BBox), sem consumir uma nova detecção."""
        now = self._clock()
        return {obj_id: data[1] for obj_id, data in self.objects.items() if self._visible(obj_id, now)}

    def _visible(self, obj_id: int, now: float) -> bool:
        age = now - self.last_seen[obj_id]
        # YOLO lento (CPU no limite, câmera de muitos megapixels): a próxima detecção demora mais
        # que a tolerância fixa e a caixa sumia entre uma e outra. A tolerância acompanha o ritmo real.
        pace = 2.5 * self.update_interval if self._intervals >= 4 else 0.0     # só com ritmo já medido
        if age <= max(self.visible_grace_sec, pace):
            return True
        hold = min(self._forget_after(), max(self.visible_hold_sec, pace))
        return age <= hold and self.misses.get(obj_id, 0) < self.misses_to_hide

    def alive_ids(self) -> set:
        """Todos os IDs ainda lembrados (inclui quem sumiu há < max_disappeared_sec)."""
        return set(self.objects.keys())

    def _gate(self, bbox: Tuple[int, int, int, int]) -> float:
        """Raio de associação: no mínimo max_distance, maior para caixas grandes."""
        h = abs(bbox[3] - bbox[1])
        return max(float(self.max_distance), 0.5 * h)

    def _forget_after(self) -> float:
        """
        Quanto tempo um ID sobrevive sem ser visto. Com a CPU cheia (YOLO a cada 1–1,5 s) os 3 s fixos
        eram só duas detecções: uma falha e a pessoa ganhava ID novo — e o nome sumia até ser
        reconhecida de novo (o "pisca" do reconhecimento). Acompanha o ritmo real, até 8 s.
        """
        if self._intervals < 4:
            return self.max_disappeared_sec
        return min(8.0, max(self.max_disappeared_sec, 4.0 * self.update_interval))

    def _expire(self, now: float) -> None:
        limit = self._forget_after()
        for object_id in [oid for oid, t in self.last_seen.items() if now - t > limit]:
            self.deregister(object_id)

    # ------------------------------------------------------------------
    def update(self, bboxes: List[Tuple[int, int, int, int]]) -> Dict[int, Tuple[int, int, int, int]]:
        """
        Recebe as caixas de UMA detecção nova do YOLO e atualiza os IDs.
        Retorna os tracks VISÍVEIS (ver current()); os que sumiram há pouco
        continuam vivos internamente (alive_ids) até max_disappeared_sec.
        """
        now = self._clock()
        if self._last_update is not None:
            gap = min(5.0, max(0.0, now - self._last_update))
            self.update_interval = gap if not self.update_interval else 0.8 * self.update_interval + 0.2 * gap
            self._intervals += 1
        self._last_update = now

        if len(bboxes) == 0:
            for oid in self.objects:
                self.misses[oid] = self.misses.get(oid, 0) + 1
            self._expire(now)
            return self.current()

        input_centroids = np.zeros((len(bboxes), 2), dtype="int")
        for i, (x1, y1, x2, y2) in enumerate(bboxes):
            input_centroids[i] = (int((x1 + x2) / 2.0), int((y1 + y2) / 2.0))

        if len(self.objects) == 0:
            for i in range(len(input_centroids)):
                self.register(tuple(input_centroids[i]), tuple(bboxes[i]), now)
            return self.current()

        object_ids = list(self.objects.keys())
        object_centroids = np.array([data[0] for data in self.objects.values()])
        dist_matrix = np.linalg.norm(object_centroids[:, np.newaxis] - input_centroids, axis=2)

        # Associação gulosa UM-PARA-UM por menor distância global (igual ao
        # EduVision): ordena TODOS os pares (track, detecção) e casa do mais
        # próximo para o mais distante, sem reaproveitar nenhum dos dois.
        gates = np.array([self._gate(self.objects[oid][1]) for oid in object_ids])
        rows, cols = np.nonzero(dist_matrix <= gates[:, np.newaxis])
        order = np.argsort(dist_matrix[rows, cols], kind="stable")

        used_rows, used_cols = set(), set()
        for k in order:
            row, col = int(rows[k]), int(cols[k])
            if row in used_rows or col in used_cols:
                continue
            object_id = object_ids[row]
            self.objects[object_id] = (tuple(input_centroids[col]), tuple(bboxes[col]))
            self.last_seen[object_id] = now
            self.misses[object_id] = 0
            used_rows.add(row)
            used_cols.add(col)

        for row, object_id in enumerate(object_ids):
            if row not in used_rows:
                self.misses[object_id] = self.misses.get(object_id, 0) + 1

        # Detecções sem par viram tracks novos
        for col in range(len(input_centroids)):
            if col not in used_cols:
                self.register(tuple(input_centroids[col]), tuple(bboxes[col]), now)

        # Quem não foi visto há mais de max_disappeared_sec é esquecido
        self._expire(now)
        return self.current()
