import time
from typing import Dict, Any, Optional, Tuple
from collections import Counter

STATUS_PENDING = "PENDING"
STATUS_RESOLVED = "RESOLVED"
STATUS_UNKNOWN = "UNKNOWN"

class IdentityManager:
    """
    [DECISÃO DO PROJETO] Gerenciador volátil de identidade e estabilização por Track.

    Regras para a identidade "segurar" na pessoa:
      * RESOLVED é pegajoso: rosto de lado/baixa similaridade (UNKNOWN) ou sem
        rosto (NO_FACE) NÃO derrubam a identidade. Só troca para OUTRO aluno
        com evidência maior que a do atual (proteção contra troca de ID).
      * Depois de resolvido, o track é reverificado com menos frequência
        (resolved_recheck_sec) — economiza InsightFace para quem ainda não
        foi identificado.
    """
    
    def __init__(
        self, 
        recognition_interval_sec: float = 1.0, 
        window_size: int = 5, 
        min_evidence_count: int = 3,
        resolved_recheck_sec: float = 5.0,
        soft_similarity: float = 0.35,
    ):
        if recognition_interval_sec <= 0:
            raise ValueError("recognition_interval_sec deve ser estritamente maior que 0.")
        if window_size < 1:
            raise ValueError("window_size deve ser no mínimo 1.")
        if min_evidence_count < 1 or min_evidence_count > window_size:
            raise ValueError("min_evidence_count deve estar entre 1 e window_size inclusive.")

        self.recognition_interval_sec = recognition_interval_sec
        self.window_size = window_size
        self.min_evidence_count = min_evidence_count
        self.resolved_recheck_sec = max(resolved_recheck_sec, recognition_interval_sec)
        self.soft_similarity = soft_similarity
        
        self.tracks_cache: Dict[Tuple[int, int], Dict[str, Any]] = {}

    def _get_or_create_track(self, camera_id: int, track_id: int, current_time: float) -> Dict[str, Any]:
        key = (camera_id, track_id)
        if key not in self.tracks_cache:
            self.tracks_cache[key] = {
                "camera_id": camera_id,
                "track_id": track_id,
                "student_id": None,
                "similarity": 0.0,
                "status": STATUS_PENDING,
                "had_face": False,   # já apareceu algum rosto neste track?
                "history": [],
                "last_attempt_time": None,
                "attempts_count": 0,
                "created_time": current_time,   # quando o track apareceu (p/ "rosto obstruído")
                "last_update_time": current_time
            }
        return self.tracks_cache[key]

    def evaluate_track(self, camera_id: int, track_id: int, current_time: Optional[float] = None) -> Dict[str, Any]:
        if current_time is None:
            current_time = time.monotonic()
            
        track_data = self._get_or_create_track(camera_id, track_id, current_time)
        
        if track_data["last_attempt_time"] is None:
            should_recognize = True
        else:
            time_elapsed = current_time - track_data["last_attempt_time"]
            interval = (self.resolved_recheck_sec if track_data["status"] == STATUS_RESOLVED
                        else self.recognition_interval_sec)
            should_recognize = time_elapsed >= interval
        
        return {
            "camera_id": camera_id,
            "track_id": track_id,
            "student_id": track_data["student_id"],
            "similarity": track_data["similarity"],
            "status": track_data["status"],
            "should_recognize": should_recognize
        }

    def get_track_identity(self, camera_id: int, track_id: int) -> Optional[int]:
        """
        [DECISÃO DO PROJETO] Retorna o student_id atual do track de forma segura,
        sem acionar lógica de throttling ou alterar o estado interno.
        """
        track_data = self.tracks_cache.get((camera_id, track_id))
        if track_data:
            return track_data.get("student_id")
        return None

    def update_track(
        self, 
        camera_id: int, 
        track_id: int, 
        recognition_result: Dict[str, Any], 
        current_time: Optional[float] = None
    ):
        if current_time is None:
            current_time = time.monotonic()
            
        track_data = self._get_or_create_track(camera_id, track_id, current_time)
        track_data["last_attempt_time"] = current_time
        track_data["attempts_count"] += 1
        track_data["last_update_time"] = current_time
        
        if recognition_result.get("type") != "NO_FACE":
            track_data["had_face"] = True
        track_data["history"].append(recognition_result)
            
        if len(track_data["history"]) > self.window_size:
            track_data["history"].pop(0)
            
        self._stabilize(track_data)

    def _stabilize(self, track_data: Dict[str, Any]):
        """
        Decide o rótulo do track a partir da janela de resultados.

        * MATCH forte (similaridade >= FACE_THRESHOLD) = evidência forte.
        * UNKNOWN cujo melhor candidato passou do limiar SUAVE (soft_similarity)
          = evidência fraca daquele aluno. Rosto de lado costuma cair aqui
          (ex.: 0,38 com a foto frontal do cadastro).
        * Resolve para o aluno X quando há ao menos 1 MATCH forte de X e
          forte + fraca de X >= min_evidence_count. Assim, quem ficou de lado
          e depois virou de frente é identificado já na primeira leitura
          frontal, sem recomeçar a contagem. Uma leitura fraca SOZINHA nunca
          identifica ninguém.
        * "Desconhecido" só depois de min_evidence_count leituras sem nenhum
          candidato plausível (antes bastava uma leitura de lado).
        * O rótulo nunca volta para "Avaliando..." (cinza): de Desconhecido ele
          só sai direto para o nome.
        """
        history = track_data["history"]
        if not history:
            return

        current_status = track_data["status"]
        current_student_id = track_data["student_id"]

        no_face_count = sum(1 for item in history if item.get("type") == "NO_FACE")
        if no_face_count >= self.window_size:
            # A identidade acompanha o track até a pessoa sair de cena: de costas,
            # abaixada ou de capacete ela continua reconhecida (RESOLVED/UNKNOWN).
            return

        match_items = [item for item in history if item.get("type") == "MATCH"]
        strong = Counter(item["student_id"] for item in match_items)
        weak_items = [item for item in history
                      if item.get("type") == "UNKNOWN" and item.get("candidate_id") is not None
                      and item.get("similarity", 0.0) >= self.soft_similarity]
        weak = Counter(item["candidate_id"] for item in weak_items)

        def support(student_id) -> int:
            return strong.get(student_id, 0) + weak.get(student_id, 0)

        def best_similarity(student_id) -> float:
            return max([i["similarity"] for i in match_items if i["student_id"] == student_id], default=0.0)

        if current_status == STATUS_RESOLVED and current_student_id is not None:
            # Pegajoso: rosto de lado/borrado não "desidentifica". Só troca para
            # OUTRO aluno com evidência forte maior que a do atual.
            if not strong:
                return
            challenger, frequency = strong.most_common(1)[0]
            if challenger == current_student_id:
                track_data["similarity"] = max(track_data["similarity"], best_similarity(challenger))
                return
            if frequency <= support(current_student_id) or frequency < self.min_evidence_count:
                return
            track_data["student_id"] = challenger
            track_data["similarity"] = best_similarity(challenger)
            return

        if strong:
            candidate = max(strong, key=lambda sid: (support(sid), strong[sid]))
            if support(candidate) >= self.min_evidence_count:
                track_data["student_id"] = candidate
                track_data["similarity"] = best_similarity(candidate)
                track_data["status"] = STATUS_RESOLVED
            return   # ainda juntando evidência: mantém o rótulo atual (sem voltar ao cinza)

        unknown_items = [item for item in history if item.get("type") == "UNKNOWN"]
        if len(unknown_items) >= self.min_evidence_count and not weak:
            track_data["student_id"] = None
            track_data["similarity"] = max(i.get("similarity", 0.0) for i in unknown_items)
            track_data["status"] = STATUS_UNKNOWN

    def reset_for_new_base(self, valid_student_ids) -> None:
        """
        Base facial mudou (cadastro/edição/exclusão). Quem está RESOLVED para um
        aluno que continua existindo mantém a identidade — antes, TODOS em cena
        voltavam a "Avaliando..." a cada cadastro. Os demais (pendentes,
        desconhecidos, aluno excluído) são reavaliados já na próxima rodada.
        """
        valid = set(valid_student_ids)
        for data in self.tracks_cache.values():
            if data["status"] == STATUS_RESOLVED and data["student_id"] in valid:
                continue
            data["status"] = STATUS_PENDING
            data["student_id"] = None
            data["similarity"] = 0.0
            data["history"] = [h for h in data["history"] if h.get("type") == "NO_FACE"]
            data["last_attempt_time"] = None

    def remove_track(self, camera_id: int, track_id: int):
        self.tracks_cache.pop((camera_id, track_id), None)