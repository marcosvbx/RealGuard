# =====================================================================
# RealGuard MVP - Consultas SQL Centralizadas
# =====================================================================

GET_RULES_BY_CAMERA = """
SELECT id, camera_id, name, trigger_event, resulting_event, severity, conditions, enabled
FROM rules
WHERE camera_id = ? AND enabled = 1
"""

GET_ZONES_BY_CAMERA = """
SELECT id, name, type, coordinates
FROM zones
WHERE camera_id = ? AND enabled = 1
"""

GET_KNOWN_EMBEDDINGS = """
SELECT id as face_id, student_id, embedding
FROM student_faces
"""

GET_PENDING_EVENTS = """
SELECT id, camera_id, event_type, severity, zone_id, track_id, student_id, description, detection_confidence, recognition_confidence, status, created_at
FROM security_events
WHERE status = 'PENDING'
ORDER BY id DESC
"""

INSERT_SECURITY_EVENT = """
INSERT INTO security_events (camera_id, event_type, severity, zone_id, track_id, student_id, description, detection_confidence, recognition_confidence, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# =====================================================================
# Alunos / Reconhecimento Facial
# =====================================================================

INSERT_STUDENT = """
INSERT INTO students (registration_number, name, class_name, telegram_chat_id, guardian_name, guardian_phone)
VALUES (?, ?, ?, ?, ?, ?)
"""

LIST_STUDENTS = """
SELECT s.id, s.registration_number, s.name, s.class_name, s.telegram_chat_id,
       s.guardian_name, s.guardian_phone, s.created_at,
       s.consent_at, s.consent_guardian, s.consent_by, s.consent_version, s.ficticio,
       (SELECT GROUP_CONCAT(g.telegram_chat_id) FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id
         WHERE sg.student_id = s.id AND g.telegram_chat_id != '') AS telegram_chats,
       (SELECT MAX(p.created_at) FROM presences p WHERE p.student_id = s.id) AS last_seen,
       COUNT(sf.id) as face_count
FROM students s
LEFT JOIN student_faces sf ON sf.student_id = s.id
GROUP BY s.id
ORDER BY s.name COLLATE NOCASE ASC
"""

GET_STUDENT_BY_ID = """
SELECT id, registration_number, name, class_name, telegram_chat_id, guardian_name, guardian_phone, telegram_link_token,
       consent_at, consent_guardian, consent_by, consent_version
FROM students
WHERE id = ?
"""

DELETE_STUDENT = """
DELETE FROM students WHERE id = ?
"""

DELETE_STUDENT_FACES = """
DELETE FROM student_faces WHERE student_id = ?
"""

INSERT_STUDENT_FACE = """
INSERT INTO student_faces (student_id, embedding)
VALUES (?, ?)
"""

COUNT_STUDENT_FACES = """
SELECT COUNT(*) as cnt FROM student_faces WHERE student_id = ?
"""

# =====================================================================
# Dashboard / Estatísticas
# =====================================================================

STATS_TOTAL_STUDENTS = "SELECT COUNT(*) as cnt FROM students"
STATS_TOTAL_FACES = "SELECT COUNT(*) as cnt FROM student_faces"
STATS_PENDING_EVENTS = "SELECT COUNT(*) as cnt FROM security_events WHERE status = 'PENDING'"
STATS_EVENTS_TODAY = "SELECT COUNT(*) as cnt FROM security_events WHERE date(created_at) = date(rg_now())"
STATS_EVENTS_TOTAL = "SELECT COUNT(*) as cnt FROM security_events"

GET_RECENT_EVENTS = """
SELECT se.id, se.camera_id, se.event_type, se.severity, se.zone_id, se.track_id,
       se.student_id, s.name as student_name, se.description, se.status, se.created_at
FROM security_events se
LEFT JOIN students s ON s.id = se.student_id
ORDER BY se.created_at DESC
LIMIT ?
"""

GET_EVENTS_LAST_24H_BY_HOUR = """
SELECT strftime('%H', created_at) as hour, COUNT(*) as cnt
FROM security_events
WHERE created_at >= datetime(rg_now(), '-24 hours')
GROUP BY hour
ORDER BY hour ASC
"""

GET_STUDENT_BY_REGISTRATION = """
SELECT id, registration_number, name, class_name, telegram_chat_id, guardian_name, guardian_phone
FROM students
WHERE registration_number = ?
"""

UPDATE_STUDENT = """
UPDATE students
SET name = ?, registration_number = ?, class_name = ?, telegram_chat_id = ?, guardian_name = ?, guardian_phone = ?
WHERE id = ?
"""

# =====================================================================
# Presenças (gatilho do Telegram)
# =====================================================================

INSERT_PRESENCE = """
INSERT INTO presences
(student_id, student_name, registration_number, camera_id, track_id, similarity,
 telegram_status, telegram_detail, direction, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, rg_now())
"""

UPDATE_PRESENCE_TELEGRAM = """
UPDATE presences SET telegram_status = ?, telegram_detail = ? WHERE id = ?
"""

LIST_PRESENCES = """
SELECT id, student_id, student_name, registration_number, camera_id, track_id,
       similarity, telegram_status, telegram_detail, direction, created_at
FROM presences
ORDER BY id DESC
LIMIT ?
"""

STATS_PRESENCES_TODAY = """
SELECT COUNT(*) as cnt FROM presences WHERE date(created_at) = date(rg_now())
"""
