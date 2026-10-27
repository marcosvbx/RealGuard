import json
import logging
import os
import re
import secrets
import sqlite3
from enum import Enum
from typing import List, Dict, Any, Optional
import numpy as np

from config.settings import DEFAULT_DB_PATH
from database.connection import get_db_connection
from database.queries import (
    GET_RULES_BY_CAMERA,
    GET_ZONES_BY_CAMERA,
    GET_KNOWN_EMBEDDINGS,
    INSERT_SECURITY_EVENT,
    GET_PENDING_EVENTS,
    INSERT_STUDENT,
    LIST_STUDENTS,
    GET_STUDENT_BY_ID,
    DELETE_STUDENT,
    DELETE_STUDENT_FACES,
    INSERT_STUDENT_FACE,
    COUNT_STUDENT_FACES,
    STATS_TOTAL_STUDENTS,
    STATS_TOTAL_FACES,
    STATS_PENDING_EVENTS,
    STATS_EVENTS_TODAY,
    STATS_EVENTS_TOTAL,
    GET_RECENT_EVENTS,
    GET_EVENTS_LAST_24H_BY_HOUR,
    GET_STUDENT_BY_REGISTRATION,
    UPDATE_STUDENT,
    INSERT_PRESENCE,
    UPDATE_PRESENCE_TELEGRAM,
    LIST_PRESENCES,
    STATS_PRESENCES_TODAY,
)
from app.recognition.insightface import FaceRecognizer

logger = logging.getLogger(__name__)

def load_rules(camera_id: int) -> List[Dict[str, Any]]:
    rules = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(GET_RULES_BY_CAMERA, (camera_id,))
            rows = cursor.fetchall()
            for row in rows:
                rule_dict = dict(row)
                conditions_raw = rule_dict.get("conditions")
                if isinstance(conditions_raw, str):
                    try:
                        rule_dict["conditions"] = json.loads(conditions_raw)
                    except Exception:
                        rule_dict["conditions"] = {}
                rules.append(rule_dict)
    except Exception as e:
        logger.error(f"Falha ao carregar regras para a câmera {camera_id}: {e}")
    return rules

def load_zones(camera_id: int) -> List[Dict[str, Any]]:
    zones = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(GET_ZONES_BY_CAMERA, (camera_id,))
            rows = cursor.fetchall()
            for row in rows:
                zone_dict = dict(row)
                coords_raw = zone_dict.get("coordinates")
                if isinstance(coords_raw, str):
                    try:
                        parsed_coords = json.loads(coords_raw)
                        zone_dict["coordinates"] = [tuple(pt) if isinstance(pt, list) else pt for pt in parsed_coords]
                    except Exception as e:
                        logger.error(f"Zona ignorada devido a coordenadas JSON inválidas (ID {zone_dict.get('id')}): {e}")
                        continue
                zones.append(zone_dict)
    except Exception as e:
        logger.error(f"Falha de I/O no banco ao carregar zonas para a câmera {camera_id}: {e}")
    return zones

def load_known_embeddings() -> List[Dict[str, Any]]:
    embeddings_list = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(GET_KNOWN_EMBEDDINGS)
            except Exception:
                cursor.execute("SELECT id as face_id, student_id, embedding FROM student_faces")
            
            rows = cursor.fetchall()
            for row in rows:
                row_dict = dict(row)
                face_id = row_dict.get("face_id")
                student_id = row_dict.get("student_id")
                blob = row_dict.get("embedding")
                
                if not blob:
                    continue
                    
                try:
                    embedding_array = FaceRecognizer.deserialize_embedding(blob)
                    if embedding_array is None or embedding_array.size == 0 or embedding_array.ndim != 1:
                        logger.error(f"Embedding vazio/inválido ignorado (Face ID {face_id}, Student ID {student_id}).")
                        continue
                    embeddings_list.append({"student_id": student_id, "embedding": embedding_array})
                except Exception as e:
                    logger.error(f"Embedding corrompido ignorado (Face ID {face_id}, Student ID {student_id}): {type(e).__name__}")
                    continue
    except Exception as e:
        logger.error(f"Falha ao carregar embeddings conhecidos: {e}")
    return embeddings_list

def save_security_event(event: Dict[str, Any], return_id: bool = False):
    """Grava um evento. Retorna True/False — ou o id do evento (None em falha) com return_id=True."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            camera_id = event.get("camera_id", 1)
            event_type = event.get("event_type", "UNKNOWN")
            severity = event.get("severity", "MEDIUM")
            zone_id = event.get("zone_id")
            track_id = event.get("track_id")
            student_id = event.get("student_id")
            description = event.get("description", "")
            detection_confidence = event.get("detection_confidence")
            recognition_confidence = event.get("recognition_confidence")
            status = event.get("status", "PENDING")

            query = """
            INSERT INTO security_events 
            (camera_id, event_type, severity, zone_id, track_id, student_id, description, detection_confidence, recognition_confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, rg_now())
            """
            cursor.execute(query, (
                camera_id, event_type, severity, zone_id, track_id, student_id, 
                description, detection_confidence, recognition_confidence, status
            ))
            conn.commit()
            return cursor.lastrowid if return_id else True
    except Exception as e:
        logger.error(f"Falha de I/O no banco ao salvar evento de segurança: {e}")
        return None if return_id else False


class EventStatus(str, Enum):
    """Estados possíveis de um alerta (security_events.status)."""
    PENDING = "PENDING"                    # aguardando decisão humana
    VALIDATED_TRUE = "VALIDATED_TRUE"      # humano confirmou a ocorrência
    VALIDATED_FALSE = "VALIDATED_FALSE"    # humano descartou (falso alarme)
    AUTO_DISMISSED = "AUTO_DISMISSED"      # o próprio sistema descartou (ex.: o rosto apareceu)


# Decisões que um humano pode tomar na tela de Ocorrências.
HUMAN_DECISIONS = (EventStatus.VALIDATED_TRUE.value, EventStatus.VALIDATED_FALSE.value)

# Status de um alerta que o próprio sistema descartou (ex.: "rosto obstruído"
# e, logo depois, o rosto apareceu). Não conta como pendente na Portaria.
STATUS_AUTO_DISMISSED = EventStatus.AUTO_DISMISSED.value

DECIDE_OK = "ok"
DECIDE_NOT_FOUND = "not_found"
DECIDE_ALREADY = "already_decided"
DECIDE_INVALID = "invalid_status"


def decide_event(event_id: int, status: str, user_id: Optional[int]) -> str:
    """
    Registra a decisão humana sobre um alerta PENDENTE. Atômico: o UPDATE só
    pega se o alerta ainda estiver pendente, então dois operadores decidindo
    ao mesmo tempo não sobrescrevem um ao outro.
    """
    if status not in HUMAN_DECISIONS:
        return DECIDE_INVALID
    with get_db_connection() as conn:
        cur = conn.execute(
            "UPDATE security_events SET status = ?, validated_by = ?, "
            "validated_at = rg_now() WHERE id = ? AND status = ?",
            (status, user_id, int(event_id), EventStatus.PENDING.value))
        conn.commit()
        if cur.rowcount:
            return DECIDE_OK
        exists = conn.execute("SELECT 1 FROM security_events WHERE id = ?", (int(event_id),)).fetchone()
    return DECIDE_ALREADY if exists else DECIDE_NOT_FOUND


def list_pending_events() -> List[Dict[str, Any]]:
    """Alertas aguardando decisão humana (consulta de polling)."""
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(GET_PENDING_EVENTS)]


def auto_dismiss_security_event(event_id: int, reason: str = "") -> bool:
    """Descarta automaticamente um evento AINDA PENDENTE (nunca sobrescreve validação humana)."""
    try:
        with get_db_connection() as conn:
            cur = conn.execute(
                "UPDATE security_events SET status = ?, "
                "description = COALESCE(description, '') || ?, validated_at = rg_now() "
                "WHERE id = ? AND status = 'PENDING'",
                (STATUS_AUTO_DISMISSED, f" [descartado automaticamente: {reason}]" if reason else "", event_id),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Falha ao descartar automaticamente o evento {event_id}: {e}")
        return False


# =====================================================================
# Alunos / Reconhecimento Facial
# =====================================================================

def create_student(
    name: str,
    registration_number: Optional[str] = None,
    telegram_chat_id: str = "",
    class_name: str = "",
    guardian_name: str = "",
    guardian_phone: str = "",
) -> Optional[int]:
    """Cria um novo aluno e retorna o ID gerado, ou None em caso de falha."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                INSERT_STUDENT,
                (registration_number, name, class_name or "", telegram_chat_id or "",
                 guardian_name or "", guardian_phone or ""),
            )
            student_id = cursor.lastrowid
            # Responsável informado no cadastro = responsável principal (tabela guardians).
            if (guardian_name or "").strip() or (guardian_phone or "").strip() or (telegram_chat_id or "").strip():
                g = conn.execute("INSERT INTO guardians (name, phone, telegram_chat_id) VALUES (?, ?, ?)",
                                 ((guardian_name or "").strip(), (guardian_phone or "").strip(),
                                  (telegram_chat_id or "").strip())).lastrowid
                conn.execute("INSERT INTO student_guardians (student_id, guardian_id, relationship, is_primary) "
                             "VALUES (?, ?, 'Responsável', 1)", (student_id, g))
            conn.commit()
            return student_id
    except Exception as e:
        logger.error(f"Falha ao criar aluno '{name}': {e}")
        return None


def get_student_by_registration(registration_number: str) -> Optional[Dict[str, Any]]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(GET_STUDENT_BY_REGISTRATION, (registration_number,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Falha ao buscar aluno pelo RM {registration_number}: {e}")
        return None


def update_student(
    student_id: int,
    name: str,
    registration_number: str,
    class_name: str = "",
    telegram_chat_id: str = "",
    guardian_name: str = "",
    guardian_phone: str = "",
) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                UPDATE_STUDENT,
                (name, registration_number, class_name or "", telegram_chat_id or "",
                 guardian_name or "", guardian_phone or "", student_id),
            )
            ok = cursor.rowcount > 0
            # Campos do "responsável principal" (forma antiga de editar): atualizam o principal.
            prim = conn.execute("SELECT guardian_id FROM student_guardians WHERE student_id = ? "
                                "ORDER BY is_primary DESC, created_at LIMIT 1", (student_id,)).fetchone()
            gname, gphone, chat = (guardian_name or "").strip(), (guardian_phone or "").strip(), \
                (telegram_chat_id or "").strip()
            if prim:
                conn.execute("UPDATE guardians SET name = ?, phone = ? WHERE id = ?", (gname, gphone, prim[0]))
                linked = conn.execute("SELECT 1 FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id "
                                      "WHERE sg.student_id = ? AND g.telegram_chat_id = ? AND ? != ''",
                                      (student_id, chat, chat)).fetchone()
                if chat and not linked:
                    conn.execute("UPDATE guardians SET telegram_chat_id = ? WHERE id = ?", (chat, prim[0]))
            elif gname or gphone or chat:
                g = conn.execute("INSERT INTO guardians (name, phone, telegram_chat_id) VALUES (?, ?, ?)",
                                 (gname, gphone, chat)).lastrowid
                conn.execute("INSERT INTO student_guardians (student_id, guardian_id, relationship, is_primary) "
                             "VALUES (?, ?, 'Responsável', 1)", (student_id, g))
            from database import guardians
            guardians.sync_student(conn, student_id)
            conn.commit()
            return ok
    except Exception as e:
        logger.error(f"Falha ao atualizar aluno {student_id}: {e}")
        return False


def list_students() -> List[Dict[str, Any]]:
    """Lista todos os alunos cadastrados com a contagem de faces (embeddings) associadas."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(LIST_STUDENTS)
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Falha ao listar alunos: {e}")
        return []


def get_student(student_id: int) -> Optional[Dict[str, Any]]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(GET_STUDENT_BY_ID, (student_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Falha ao buscar aluno {student_id}: {e}")
        return None


def delete_student(student_id: int) -> bool:
    """Remove um aluno e todas as faces associadas (exclusão manual, sem depender de FK PRAGMA)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            from database import guardians
            guardians.remove_student(conn, student_id)
            from database import pickups as _pk
            _pk.remove_student(conn, student_id)
            cursor.execute(DELETE_STUDENT_FACES, (student_id,))
            cursor.execute(DELETE_STUDENT, (student_id,))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Falha ao remover aluno {student_id}: {e}")
        return False


def add_student_face(student_id: int, embedding) -> Optional[int]:
    """Persiste um novo embedding facial (numpy array) associado a um aluno."""
    try:
        blob = FaceRecognizer.serialize_embedding(embedding)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(INSERT_STUDENT_FACE, (student_id, blob))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Falha ao salvar face do aluno {student_id}: {e}")
        return None


def count_student_faces(student_id: int) -> int:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(COUNT_STUDENT_FACES, (student_id,))
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0
    except Exception as e:
        logger.error(f"Falha ao contar faces do aluno {student_id}: {e}")
        return 0


def clear_student_faces(student_id: int) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(DELETE_STUDENT_FACES, (student_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Falha ao limpar faces do aluno {student_id}: {e}")
        return False


# =====================================================================
# Dashboard / Estatísticas
# =====================================================================

def get_dashboard_stats() -> Dict[str, Any]:
    """Agrega os números usados pelos cartões de KPI do painel web."""
    stats = {
        "total_students": 0,
        "total_faces": 0,
        "pending_events": 0,
        "events_today": 0,
        "events_total": 0,
        "presences_today": 0,
    }
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(STATS_TOTAL_STUDENTS)
            stats["total_students"] = cursor.fetchone()["cnt"]

            cursor.execute(STATS_TOTAL_FACES)
            stats["total_faces"] = cursor.fetchone()["cnt"]

            cursor.execute(STATS_PENDING_EVENTS)
            stats["pending_events"] = cursor.fetchone()["cnt"]

            cursor.execute(STATS_EVENTS_TODAY)
            stats["events_today"] = cursor.fetchone()["cnt"]

            cursor.execute(STATS_EVENTS_TOTAL)
            stats["events_total"] = cursor.fetchone()["cnt"]

            cursor.execute(STATS_PRESENCES_TODAY)
            stats["presences_today"] = cursor.fetchone()["cnt"]
    except Exception as e:
        logger.error(f"Falha ao calcular estatísticas do dashboard: {e}")
    return stats


def get_recent_events(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(GET_RECENT_EVENTS, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Falha ao carregar eventos recentes: {e}")
        return []


def get_events_last_24h_by_hour() -> List[Dict[str, Any]]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(GET_EVENTS_LAST_24H_BY_HOUR)
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Falha ao agregar eventos por hora: {e}")
        return []


# =====================================================================
# Presenças
# =====================================================================

def register_presence(
    student_id: Optional[int],
    student_name: str,
    registration_number: Optional[str],
    camera_id: int,
    track_id: Optional[int],
    similarity: Optional[float],
    telegram_status: str = "PENDING",
    telegram_detail: str = "",
    direction: str = "ENTRY",
) -> Optional[int]:
    """Grava uma presença reconhecida e devolve o id da linha (ou None em falha)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(INSERT_PRESENCE, (
                student_id, student_name, registration_number, camera_id,
                track_id, similarity, telegram_status, telegram_detail, direction,
            ))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Falha ao registrar presença de '{student_name}': {e}")
        return None


def update_presence_telegram(presence_id: int, status: str, detail: str = "") -> bool:
    try:
        with get_db_connection() as conn:
            conn.execute(UPDATE_PRESENCE_TELEGRAM, (status, detail[:300], presence_id))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Falha ao atualizar status do Telegram da presença {presence_id}: {e}")
        return False


def list_presences(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(LIST_PRESENCES, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Falha ao listar presenças: {e}")
        return []


# =====================================================================
# Vínculo do Telegram (sem precisar descobrir Chat ID à mão)
# =====================================================================

def normalize_phone(value: str) -> str:
    """Só dígitos, sem o DDI 55 do Brasil: '(11) 98765-4321' e '+55 11 98765-4321' -> '11987654321'."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) > 11 and digits.startswith("55"):
        digits = digits[2:]
    return digits.lstrip("0")


def phones_match(a: str, b: str) -> bool:
    """
    Compara dois celulares brasileiros. Tolera o 9º dígito ausente num dos
    lados (cadastros antigos): exige DDD igual e os 8 últimos dígitos iguais.
    """
    x, y = normalize_phone(a), normalize_phone(b)
    if len(x) < 10 or len(y) < 10:
        return False
    if x == y:
        return True
    return x[:2] == y[:2] and x[-8:] == y[-8:]


def _primary_guardian(student_id: int, create: bool = False) -> Optional[int]:
    from database import guardians
    with get_db_connection() as conn:
        row = conn.execute("SELECT guardian_id FROM student_guardians WHERE student_id = ? "
                           "ORDER BY is_primary DESC, created_at LIMIT 1", (int(student_id),)).fetchone()
        if row:
            return row[0]
        if not create or not conn.execute("SELECT 1 FROM students WHERE id = ?", (int(student_id),)).fetchone():
            return None
    gid = guardians.create("Responsável")
    guardians.link(student_id, gid, "Responsável", primary=True)
    return gid


def ensure_link_token(student_id: int) -> Optional[str]:
    """Convite do responsável PRINCIPAL do aluno (forma antiga; a tela usa o convite de cada responsável)."""
    from database import guardians
    try:
        gid = _primary_guardian(student_id, create=True)
        return guardians.ensure_link_token(gid) if gid else None
    except Exception as e:
        logger.error(f"Falha ao gerar convite do Telegram do aluno {student_id}: {e}")
        return None


def link_telegram_by_token(token: str, chat_id: str) -> Optional[Dict[str, Any]]:
    """Convite aberto pelo responsável: grava o chat_id no responsável do código. Devolve o 1º aluno + 'alunos'."""
    from database import guardians
    try:
        g = guardians.link_by_token(token, chat_id)
    except Exception as e:
        logger.error(f"Falha ao vincular Telegram por convite: {e}")
        return None
    if not g:
        return None
    students = g.get("alunos") or []
    first = dict(students[0]) if students else {"id": None, "name": g.get("name") or "", "class_name": ""}
    first["alunos"] = students
    first["responsavel"] = g.get("name") or ""
    return first


def link_telegram_by_phone(phone: str, chat_id: str) -> List[Dict[str, Any]]:
    """Número compartilhado no bot: vincula todos os responsáveis com esse número (e, assim, todos os filhos)."""
    from database import guardians
    try:
        return guardians.link_by_phone(phone, chat_id)
    except Exception as e:
        logger.error(f"Falha ao vincular Telegram por telefone: {e}")
        return []


def unlink_telegram(student_id: int) -> bool:
    """Desvincula o Telegram de TODOS os responsáveis do aluno."""
    from database import guardians
    try:
        gids = [g["id"] for g in guardians.for_student(student_id)]
        for gid in gids:
            guardians.unlink_telegram(gid)
        return bool(gids)
    except Exception as e:
        logger.error(f"Falha ao desvincular Telegram do aluno {student_id}: {e}")
        return False


def upsert_telegram_chat(chat_id: str, name: str = "", username: str = "", phone: str = "") -> None:
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO telegram_chats (chat_id, name, username, phone, updated_at) "
                "VALUES (?, ?, ?, ?, rg_now()) "
                "ON CONFLICT(chat_id) DO UPDATE SET name = excluded.name, username = excluded.username, "
                "phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE telegram_chats.phone END, "
                "updated_at = excluded.updated_at",
                (str(chat_id), name or "", username or "", phone or ""),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Falha ao registrar chat do Telegram {chat_id}: {e}")


def list_telegram_chats(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT c.chat_id, c.name AS nome, c.username, c.phone, c.updated_at, "
                "(SELECT GROUP_CONCAT(s.name, ', ') FROM students s WHERE s.telegram_chat_id = c.chat_id) AS alunos "
                "FROM telegram_chats c ORDER BY c.updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Falha ao listar chats do Telegram: {e}")
        return []


# =====================================================================
# Foto dos alertas (evidência para a portaria decidir)
# =====================================================================

def delete_event(event_id: int) -> Optional[Dict[str, Any]]:
    """
    Exclui uma ocorrência JÁ DECIDIDA (e a foto). Devolve o que foi apagado (para a auditoria),
    None se não existe. Pendente não sai: precisa ser decidida antes (ValueError).
    """
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, event_type, status, created_at FROM security_events WHERE id = ?",
                           (int(event_id),)).fetchone()
        if not row:
            return None
        if row["status"] == "PENDING":
            raise ValueError("Esta ocorrência ainda aguarda decisão. Confirme ou descarte antes de excluir.")
        conn.execute("DELETE FROM security_events WHERE id = ?", (int(event_id),))
        conn.commit()
    try:
        os.remove(event_snapshot_path(event_id))
    except OSError:
        pass
    return dict(row)


def event_snapshot_path(event_id: int) -> str:
    base = os.path.dirname(os.path.abspath(os.getenv("DB_PATH", DEFAULT_DB_PATH)))
    return os.path.join(base, "events", f"{int(event_id)}.jpg")


def save_event_snapshot(event_id: int, photo_fn) -> bool:
    """Grava o quadro do momento do alerta (só acontece quando há alerta — raro)."""
    try:
        data = photo_fn() if callable(photo_fn) else photo_fn
        if not data:
            return False
        path = event_snapshot_path(event_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        logger.error(f"Falha ao salvar foto do evento {event_id}: {e}")
        return False


def list_events(status: Optional[str] = None, since_days: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
    """Eventos para a tela de Ocorrências (pendentes ou já resolvidos)."""
    try:
        sql = ("SELECT se.id, se.event_type, se.severity, se.track_id, se.description, se.status, "
               "se.created_at, se.validated_at, se.ficticio, se.category, se.note, se.reminded_at, "
               "se.decided_via, u.full_name AS validated_by_name, s.name AS student_name FROM security_events se "
               "LEFT JOIN users u ON u.id = se.validated_by "
               "LEFT JOIN students s ON s.id = se.student_id WHERE 1=1")
        params: list = []
        if status == "PENDING":
            # Pendência não expira na virada do dia. A Visão Geral conta todas.
            sql += " AND se.status = 'PENDING'"
        else:
            sql += " AND date(se.created_at) >= date(rg_now(), ?)"
            params.append(f"-{int(since_days)} days")
            if status == "RESOLVED":
                sql += " AND se.status != 'PENDING'"
        sql += " ORDER BY se.id DESC"
        if status != "PENDING":
            sql += " LIMIT ?"
            params.append(limit)
        with get_db_connection() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]
        for r in rows:
            r["has_snapshot"] = os.path.isfile(event_snapshot_path(r["id"]))
        return rows
    except Exception as e:
        logger.error(f"Falha ao listar eventos: {e}")
        return []


# =====================================================================
# Câmeras (cadastro). A senha RTSP NUNCA é gravada no banco: fica só no
# config/realguard.env (câmera padrão) ou é digitada na hora do teste.
# =====================================================================

CAMERA_FIELDS = ("name", "location", "source_kind", "webcam_index", "host", "port", "path", "username", "direction")


def _camera_display(c: Dict[str, Any]) -> str:
    if c.get("source_kind") == "webcam":
        return f"webcam {c.get('webcam_index', 0)}"
    user = f"{c['username']}:***@" if c.get("username") else ""
    return f"rtsp://{user}{c.get('host', '')}:{c.get('port', 554)}{c.get('path', '')}"


def list_cameras() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, location, source_kind, webcam_index, host, port, path, username, direction, enabled, created_at, "
            "ficticio FROM cameras ORDER BY id").fetchall()
    cams = [dict(r) for r in rows]
    for c in cams:
        c["fonte"] = _camera_display(c)
    return cams


def get_camera(camera_id: int) -> Optional[Dict[str, Any]]:
    for c in list_cameras():
        if c["id"] == int(camera_id):
            return c
    return None


def create_camera(data: Dict[str, Any]) -> int:
    values = [data.get(k) for k in CAMERA_FIELDS]
    with get_db_connection() as conn:
        cur = conn.execute(
            f"INSERT INTO cameras (rtsp_url, enabled, created_at, {', '.join(CAMERA_FIELDS)}) "
            f"VALUES ('cadastro', 1, rg_now(), {', '.join('?' for _ in CAMERA_FIELDS)})", values)
        conn.commit()
        return int(cur.lastrowid)


def update_camera(camera_id: int, data: Dict[str, Any]) -> bool:
    with get_db_connection() as conn:
        cur = conn.execute(f"UPDATE cameras SET {', '.join(k + ' = ?' for k in CAMERA_FIELDS)} WHERE id = ?",
                           [data.get(k) for k in CAMERA_FIELDS] + [int(camera_id)])
        conn.commit()
        return cur.rowcount > 0


def delete_camera(camera_id: int) -> bool:
    """Apaga só o cadastro (zonas/regras dela vão junto); presenças e alertas ficam no histórico."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM zones WHERE camera_id = ?", (int(camera_id),))
        conn.execute("DELETE FROM rules WHERE camera_id = ?", (int(camera_id),))
        cur = conn.execute("DELETE FROM cameras WHERE id = ?", (int(camera_id),))
        conn.commit()
        return cur.rowcount > 0


# =====================================================================
# Consentimento da biometria (Fase 1) e direção da câmera
# =====================================================================

def set_consent(student_id: int, guardian: str, registered_by: str, version: int) -> bool:
    with get_db_connection() as conn:
        cur = conn.execute(
            "UPDATE students SET consent_at = rg_now(), consent_guardian = ?, consent_by = ?, "
            "consent_version = ? WHERE id = ?", ((guardian or "").strip()[:80], (registered_by or "")[:80],
                                                 int(version), int(student_id)))
        conn.commit()
        return cur.rowcount > 0


def revoke_consent(student_id: int) -> bool:
    """Revogação: apaga o consentimento E os vetores faciais do aluno (LGPD)."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM student_faces WHERE student_id = ?", (int(student_id),))
        cur = conn.execute("UPDATE students SET consent_at = NULL, consent_guardian = '', consent_by = '', "
                           "consent_version = NULL WHERE id = ?", (int(student_id),))
        conn.commit()
        return cur.rowcount > 0


CAMERA_DIRECTIONS = ("AUTO", "ENTRY", "EXIT")


def get_camera_direction(camera_id: int) -> str:
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT direction FROM cameras WHERE id = ?", (int(camera_id),)).fetchone()
        return row[0] if row and row[0] in CAMERA_DIRECTIONS else "AUTO"
    except Exception:
        return "AUTO"


def set_camera_direction(camera_id: int, direction: str) -> bool:
    if direction not in CAMERA_DIRECTIONS:
        return False
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE cameras SET direction = ? WHERE id = ?", (direction, int(camera_id)))
        conn.commit()
        return cur.rowcount > 0


def last_passage_today(student_id: int) -> Optional[Dict[str, Any]]:
    """Última passagem do aluno hoje (direção e horário) — base da câmera em modo Automático."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT direction, created_at FROM presences WHERE student_id = ? "
            "AND date(created_at) = date(rg_now()) ORDER BY id DESC LIMIT 1", (int(student_id),)).fetchone()
    return dict(row) if row else None
