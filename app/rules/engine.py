import logging
from typing import List, Dict, Any, Tuple
import time

logger = logging.getLogger(__name__)

# [DECISÃO DO PROJETO] Estados explícitos da ocorrência para deduplicação
STATE_WAITING_THRESHOLD = "WAITING_THRESHOLD"
STATE_ALERTED = "ALERTED"

class RuleEngine:
    """
    Motor de Regras Contextuais do RealGuard.
    Transforma eventos atômicos (ZONE_ENTRY, ZONE_EXIT) em Eventos de Negócio
    utilizando uma máquina de estados e thresholds temporais.
    """
    
    def __init__(self, rules_config: List[Dict[str, Any]]):
        """
        rules_config simula as regras que virão do SQLite futuramente.
        Formato esperado:
        [
            {
                "id": 1,
                "name": "Permanência em Zona Restrita",
                "trigger_event": "ZONE_ENTRY",
                "resulting_event": "UNAUTHORIZED_ZONE",
                "severity": "ORANGE",
                "conditions": {"time_threshold_sec": 5}
            }
        ]
        """
        self.rules = rules_config
        
        # Máquina de estados em memória.
        # Chave: (camera_id, track_id, zone_id, rule_id)
        # Valor: {"state": STATE_WAITING_THRESHOLD, "entry_time": float}
        self.active_occurrences: Dict[Tuple[int, int, int, int], Dict[str, Any]] = {}

    def process(self, spatial_events: List[Dict[str, Any]], current_time: float = None) -> List[Dict[str, Any]]:
        """
        Recebe os eventos atômicos de um ou mais frames e atualiza a máquina de estados.
        Emite eventos de negócio caso os thresholds sejam atingidos.
        current_time pode ser injetado (útil para testes unitários).
        """
        if current_time is None:
            current_time = time.monotonic()
            
        business_events = []

        # 1. Processar os eventos atômicos recebidos
        for event in spatial_events:
            camera_id = event["camera_id"]
            track_id = event["track_id"]
            zone_id = event["zone_id"]
            event_type = event["event_type"]

            for rule in self.rules:
                rule_id = rule["id"]
                state_key = (camera_id, track_id, zone_id, rule_id)

                # Avalia entrada
                if event_type == rule["trigger_event"] and event_type == "ZONE_ENTRY":
                    if state_key not in self.active_occurrences:
                        self.active_occurrences[state_key] = {
                            "state": STATE_WAITING_THRESHOLD,
                            "entry_time": current_time
                        }
                
                # Avalia saída
                elif event_type == "ZONE_EXIT":
                    # Se o evento de saída pertence a uma ocorrência ativa, encerramos a ocorrência
                    if state_key in self.active_occurrences:
                        del self.active_occurrences[state_key]

        # 2. Avaliar thresholds das ocorrências ativas (Tick)
        for state_key, occurrence in self.active_occurrences.items():
            if occurrence["state"] == STATE_WAITING_THRESHOLD:
                
                camera_id, track_id, zone_id, rule_id = state_key
                
                # Busca a regra (no MVP iteramos em memória, muito rápido)
                rule = next((r for r in self.rules if r["id"] == rule_id), None)
                if not rule:
                    continue
                
                threshold = rule["conditions"].get("time_threshold_sec", 0)
                elapsed_time = current_time - occurrence["entry_time"]
                
                if elapsed_time >= threshold:
                    # Threshold atingido: Gera o Evento de Negócio e muda o estado
                    occurrence["state"] = STATE_ALERTED
                    
                    business_event = {
                        "event_type": rule["resulting_event"],
                        "camera_id": camera_id,
                        "zone_id": zone_id,
                        "track_id": track_id,
                        "severity": rule["severity"],
                        "description": f"Regra violada: {rule['name']}. Permanência de {elapsed_time:.1f}s.",
                        "timestamp": current_time # Para persistência, será convertido para ISO 8601
                    }
                    business_events.append(business_event)
                    
        return business_events