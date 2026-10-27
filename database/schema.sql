-- =====================================================================
-- RealGuard MVP - Schema Completo do Banco de Dados SQLite
-- =====================================================================

-- Tabela de Câmeras
CREATE TABLE IF NOT EXISTS cameras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rtsp_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

-- Tabela de Zonas de Monitoramento por Câmera
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    type TEXT DEFAULT 'DEFAULT',
    coordinates TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);

-- Tabela de Regras de Segurança
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,
    trigger_event TEXT NOT NULL,
    resulting_event TEXT NOT NULL,
    severity TEXT NOT NULL,
    conditions TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);

-- Tabela de Usuários (Operadores que validam eventos)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL
);

-- Tabela de Alunos
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_number TEXT,
    name TEXT NOT NULL,
    class_name TEXT DEFAULT '',
    telegram_chat_id TEXT DEFAULT '',
    guardian_name TEXT DEFAULT '',
    guardian_phone TEXT DEFAULT '',
    telegram_link_token TEXT DEFAULT '',
    created_at DATETIME DEFAULT (datetime('now','localtime'))
);

-- Tabela de Faces/Embeddings (BLOB para o numpy.tobytes())
CREATE TABLE IF NOT EXISTS student_faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);

-- Tabela de Eventos de Segurança Registrados (Alinhada 1:1 com database/queries.py)
CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    zone_id INTEGER,
    track_id INTEGER,
    student_id INTEGER,
    description TEXT,
    detection_confidence REAL,
    recognition_confidence REAL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    validated_by INTEGER,
    validated_at DATETIME,
    created_at DATETIME DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);

-- Registro de presença: uma linha por "aparição" reconhecida na câmera.
-- É este registro que dispara o aviso no Telegram.
CREATE TABLE IF NOT EXISTS presences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    student_name TEXT NOT NULL,
    registration_number TEXT,
    camera_id INTEGER NOT NULL,
    track_id INTEGER,
    similarity REAL,
    direction TEXT NOT NULL DEFAULT 'ENTRY',
    telegram_status TEXT NOT NULL DEFAULT 'PENDING',
    telegram_detail TEXT DEFAULT '',
    created_at DATETIME DEFAULT (datetime('now','localtime'))
);

-- Quem já conversou com o bot (preenchido pelo próprio bot, que recebe as
-- mensagens). Substitui a consulta direta ao getUpdates, que é consumido
-- pelo vínculo automático.
CREATE TABLE IF NOT EXISTS telegram_chats (
    chat_id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    username TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    updated_at DATETIME DEFAULT (datetime('now','localtime'))
);
