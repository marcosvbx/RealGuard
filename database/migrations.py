"""
Inicialização e migração versionada do banco.

Chamado uma vez na subida do sistema (e pelos comandos de terminal).

Como funciona:
  * `schema_version` guarda quais migrações já rodaram neste banco;
  * cada migração tem um número e roda UMA vez, em ordem, dentro de uma
    transação — banco novo ou antigo chega ao mesmo schema final;
  * `schema.sql` é o schema BASE (versão 1). Toda mudança posterior entra
    como uma nova função numerada em MIGRATIONS, nunca editando a anterior.

Quem já tem um realguard.db antigo só precisa copiá-lo para data/ e subir
o sistema: as migrações que faltam são aplicadas sozinhas.
"""

import json
import logging
import os
import sqlite3
from typing import Callable, Dict, List, Tuple

from config.settings import BASE_DIR
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
LEGACY_TELEGRAM_JSON = os.path.join(BASE_DIR, "config", "telegram_contacts.json")

# Colunas adicionadas antes do controle de versão (bancos muito antigos).
_LEGACY_COLUMNS: Dict[str, Dict[str, str]] = {
    "students": {
        "class_name": "TEXT DEFAULT ''",
        "telegram_chat_id": "TEXT DEFAULT ''",
        "created_at": "DATETIME",
        "guardian_name": "TEXT DEFAULT ''",
        "guardian_phone": "TEXT DEFAULT ''",
        "telegram_link_token": "TEXT DEFAULT ''",
    },
    "presences": {"direction": "TEXT NOT NULL DEFAULT 'ENTRY'"},
}


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = _columns(conn, table)
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            logger.info(f"Migração: coluna {table}.{name} criada.")


def _run_script(conn: sqlite3.Connection, script: str) -> None:
    """
    Executa um script SQL comando a comando. Diferente de executescript(),
    NÃO faz COMMIT antes — assim a migração inteira fica numa transação só
    e é desfeita por completo se algo falhar no meio.
    """
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement and not all(l.strip().startswith("--") or not l.strip() for l in statement.splitlines()):
                conn.execute(statement)
    if buffer.strip() and not all(l.strip().startswith("--") or not l.strip() for l in buffer.splitlines()):
        conn.execute(buffer)


# ---------------------------------------------------------------- migrações
def _m1_base(conn: sqlite3.Connection) -> None:
    """Schema base (schema.sql) + colunas de versões anteriores ao controle de versão."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        _run_script(conn, f.read())
    for table, cols in _LEGACY_COLUMNS.items():
        _add_columns(conn, table, cols)


def _m2_indices(conn: sqlite3.Connection) -> None:
    """Índices das consultas do dashboard, relatórios e do loop de presença."""
    _run_script(conn, """
        CREATE INDEX IF NOT EXISTS idx_presences_created ON presences(created_at);
        CREATE INDEX IF NOT EXISTS idx_presences_student_created ON presences(student_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_presences_camera_created ON presences(camera_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_status_created ON security_events(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_created ON security_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_student ON security_events(student_id);
        CREATE INDEX IF NOT EXISTS idx_events_camera_created ON security_events(camera_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_faces_student ON student_faces(student_id);
        CREATE INDEX IF NOT EXISTS idx_students_registration ON students(registration_number);
    """)


def _m3_acesso(conn: sqlite3.Connection) -> None:
    """Login, perfis de acesso, sessões e trilha de auditoria."""
    _add_columns(conn, "users", {
        "full_name": "TEXT DEFAULT ''",
        "password_hash": "TEXT DEFAULT ''",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "must_change_password": "INTEGER NOT NULL DEFAULT 0",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
        "last_login_at": "DATETIME",
    })
    _run_script(conn, """
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            is_system INTEGER NOT NULL DEFAULT 0,
            permissions TEXT NOT NULL DEFAULT '[]',
            created_at DATETIME DEFAULT (datetime('now','localtime')),
            updated_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        -- Só o HASH do token fica no banco: um backup vazado não dá acesso.
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT (datetime('now','localtime')),
            expires_at DATETIME NOT NULL,
            last_seen_at DATETIME DEFAULT (datetime('now','localtime')),
            ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT (datetime('now','localtime')),
            user_id INTEGER,
            username TEXT DEFAULT '',
            action TEXT NOT NULL,
            target_type TEXT DEFAULT '',
            target_id TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            ip TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
    """)


def _m4_cameras(conn: sqlite3.Connection) -> None:
    """Cadastro de várias câmeras (a senha RTSP nunca vai para o banco)."""
    _add_columns(conn, "cameras", {
        "location": "TEXT DEFAULT ''",
        "source_kind": "TEXT NOT NULL DEFAULT 'rtsp'",
        "webcam_index": "INTEGER NOT NULL DEFAULT 0",
        "host": "TEXT DEFAULT ''",
        "port": "INTEGER NOT NULL DEFAULT 554",
        "path": "TEXT DEFAULT ''",
        "username": "TEXT DEFAULT ''",
        "created_at": "DATETIME",
    })


def _m5_escola(conn: sqlite3.Connection) -> None:
    """Fase 1: turnos, turmas, calendário, regras, presença do dia, função da câmera e consentimento."""
    _run_script(conn, """
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            start_time TEXT NOT NULL,              -- HH:MM início das aulas
            tolerance_min INTEGER NOT NULL DEFAULT 15,
            end_time TEXT NOT NULL,                -- HH:MM fim das aulas
            active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- = students.class_name
            shift_id INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (shift_id) REFERENCES shifts(id)
        );
        -- FERIADO / RECESSO / SEM_AULA tiram o dia; DIA_LETIVO acrescenta (ex.: sábado letivo).
        -- shift_id vazio = vale para a escola toda.
        CREATE TABLE IF NOT EXISTS calendar_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            kind TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            shift_id INTEGER,
            source TEXT NOT NULL DEFAULT 'escola',      -- nacional | escola
            created_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_calendar_dates ON calendar_entries(start_date, end_date);
        CREATE TABLE IF NOT EXISTS school_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        -- Presença consolidada: uma linha por aluno por dia letivo. As passagens
        -- da câmera (presences) continuam intactas; correção humana fica em manual_*.
        CREATE TABLE IF NOT EXISTS attendance_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            shift_id INTEGER,
            class_name TEXT DEFAULT '',
            deadline TEXT NOT NULL,                 -- início + tolerância (HH:MM) no dia
            shift_end TEXT NOT NULL,
            cam_entry TEXT,                         -- 1ª entrada vista pela câmera (HH:MM)
            cam_exit TEXT,                          -- última saída vista pela câmera
            last_direction TEXT,
            manual_entry TEXT,
            manual_status TEXT,                     -- JUSTIFICADA
            manual_reason TEXT DEFAULT '',
            manual_by INTEGER,
            manual_by_name TEXT DEFAULT '',
            manual_at DATETIME,
            status TEXT NOT NULL,                   -- PRESENTE | ATRASADO | AUSENTE | JUSTIFICADA | PENDENTE
            early_exit INTEGER NOT NULL DEFAULT 0,
            closed INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT (datetime('now','localtime')),
            UNIQUE (student_id, day)
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_day ON attendance_days(day);
        CREATE INDEX IF NOT EXISTS idx_attendance_day_class ON attendance_days(day, class_name);
    """)
    _add_columns(conn, "cameras", {"direction": "TEXT NOT NULL DEFAULT 'AUTO'"})
    _add_columns(conn, "students", {
        "consent_at": "DATETIME",
        "consent_guardian": "TEXT DEFAULT ''",
        "consent_by": "TEXT DEFAULT ''",
        "consent_version": "INTEGER",
    })
    from database import school
    school.seed_defaults(conn)


def _m6_ficticios_integracoes(conn: sqlite3.Connection) -> None:
    """Marca de registros fictícios (removíveis pela tela) e configurações do sistema (ex.: Telegram)."""
    for table in ("students", "classes", "calendar_entries", "cameras", "security_events"):
        _add_columns(conn, table, {"ficticio": "INTEGER NOT NULL DEFAULT 0"})
    _run_script(conn, """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at DATETIME DEFAULT (datetime('now','localtime')),
            updated_by TEXT DEFAULT ''
        );
    """)


def _m7_responsaveis_ocorrencias(conn: sqlite3.Connection) -> None:
    """Vários responsáveis por aluno (parentesco, foto, Telegram de cada um) e detalhes da ocorrência."""
    _run_script(conn, """
        CREATE TABLE IF NOT EXISTS guardians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            telegram_chat_id TEXT NOT NULL DEFAULT '',
            telegram_link_token TEXT NOT NULL DEFAULT '',
            ficticio INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS student_guardians (
            student_id INTEGER NOT NULL,
            guardian_id INTEGER NOT NULL,
            relationship TEXT NOT NULL DEFAULT '',
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (student_id, guardian_id)
        );
        CREATE INDEX IF NOT EXISTS idx_student_guardians_guardian ON student_guardians(guardian_id);
        CREATE INDEX IF NOT EXISTS idx_guardians_token ON guardians(telegram_link_token);
    """)
    # Quem já tinha responsável (campos antigos do aluno) vira o responsável nº 1, com o mesmo Telegram.
    cols = _columns(conn, "students")
    fic = "ficticio" if "ficticio" in cols else "0"
    rows = conn.execute(
        f"SELECT id, COALESCE(guardian_name,''), COALESCE(guardian_phone,''), COALESCE(telegram_chat_id,''), "
        f"COALESCE(telegram_link_token,''), {fic} FROM students").fetchall()
    for sid, name, phone, chat, token, is_fake in rows:
        if not (name.strip() or phone.strip() or chat.strip() or token.strip()):
            continue
        cur = conn.execute("INSERT INTO guardians (name, phone, telegram_chat_id, telegram_link_token, ficticio) "
                           "VALUES (?, ?, ?, ?, ?)", (name.strip(), phone.strip(), chat.strip(), token.strip(), is_fake))
        conn.execute("INSERT INTO student_guardians (student_id, guardian_id, relationship, is_primary) "
                     "VALUES (?, ?, 'Responsável', 1)", (sid, cur.lastrowid))
    _add_columns(conn, "security_events", {
        "category": "TEXT DEFAULT ''",          # o que era (preenchido na confirmação)
        "note": "TEXT DEFAULT ''",
        "reminded_at": "DATETIME",              # lembrete de "ainda aguardando decisão" já enviado
    })


def _m8_bot_seguranca(conn: sqlite3.Connection) -> None:
    """Bot de segurança com botões: conta do RealGuard ligada ao Telegram e cartões de cada alerta."""
    _add_columns(conn, "users", {
        "telegram_user_id": "TEXT NOT NULL DEFAULT ''",   # quem toca no botão = este usuário
        "telegram_name": "TEXT NOT NULL DEFAULT ''",
        "telegram_link_code": "TEXT NOT NULL DEFAULT ''", # código de uso único (/start us…)
        "telegram_code_expires": "TEXT NOT NULL DEFAULT ''",
    })
    _add_columns(conn, "security_events", {
        "tg_msgs": "TEXT NOT NULL DEFAULT ''",            # {chat_id: message_id} dos cartões enviados
        "decided_via": "TEXT NOT NULL DEFAULT ''",        # painel | telegram
    })


def _m9_alertas_individuais(conn: sqlite3.Connection) -> None:
    """Alertas de segurança só no privado de quem tem conta vinculada (cada um liga/desliga os seus)."""
    _add_columns(conn, "users", {"telegram_alerts": "INTEGER NOT NULL DEFAULT 1"})


def _m10_retiradas_preferencias(conn: sqlite3.Connection) -> None:
    """Retirada autorizada pelos responsáveis (pelo Telegram) e o que cada responsável quer receber."""
    _run_script(conn, """
        CREATE TABLE IF NOT EXISTS pickup_authorizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            day TEXT NOT NULL,                          -- AAAA-MM-DD (vale só neste dia)
            person_name TEXT NOT NULL,
            relationship TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',     -- PENDING | ACTIVE | REJECTED | CANCELLED
            requested_by INTEGER,                       -- guardians.id de quem pediu
            requested_chat TEXT NOT NULL DEFAULT '',
            decided_by INTEGER,                         -- guardians.id do principal que aprovou/recusou
            decided_at DATETIME,
            cancelled_by TEXT NOT NULL DEFAULT '',      -- "responsável: Fulano" | "escola: usuário"
            has_photo INTEGER NOT NULL DEFAULT 0,
            approval_msgs TEXT NOT NULL DEFAULT '',     -- {chat: message_id} dos pedidos de aprovação
            ficticio INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_pickup_student_day ON pickup_authorizations(student_id, day);
    """)
    _add_columns(conn, "guardians", {
        "notify_entry": "INTEGER NOT NULL DEFAULT 1",
        "notify_exit": "INTEGER NOT NULL DEFAULT 1",
        "notify_absence": "INTEGER NOT NULL DEFAULT 1",
    })


def _m11_historico_avisos(conn: sqlite3.Connection) -> None:
    """Histórico de avisos aos responsáveis: uma linha por mensagem (aluno × responsável × tipo)."""
    _run_script(conn, """
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            student_name TEXT NOT NULL DEFAULT '',
            guardian TEXT NOT NULL DEFAULT '',          -- "Nome (parentesco)" no momento do envio
            chat_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,                         -- ENTRY | LATE | EXIT | ABSENCE | EARLY_EXIT
            channel TEXT NOT NULL DEFAULT 'telegram',
            status TEXT NOT NULL DEFAULT 'PENDING',     -- PENDING | SENT | FAILED
            detail TEXT NOT NULL DEFAULT '',
            sent_by TEXT NOT NULL DEFAULT '',           -- "" = automático; senão o usuário da portaria
            created_at DATETIME DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_notification_log_day ON notification_log(created_at);
    """)


def _m12_sem_cameras_exemplo(conn: sqlite3.Connection) -> None:
    """Remove as câmeras de EXEMPLO criadas pelos dados fictícios (inativas). As reais ficam."""
    keep = int(os.getenv("CAMERA_ID", "1") or 1)
    if "ficticio" in _columns(conn, "cameras"):
        conn.execute("DELETE FROM cameras WHERE ficticio = 1 AND id != ? AND COALESCE(enabled, 0) = 0", (keep,))


def _m13_portaria_e_seguranca(conn: sqlite3.Connection) -> None:
    """Portaria e Segurança viram um perfil só (na escola, quem fica no portão é quem olha as câmeras)."""
    conn.execute("UPDATE users SET role = 'operador' WHERE role = 'seguranca'")
    conn.execute("DELETE FROM roles WHERE key = 'seguranca'")


def _m15_sem_perfil_suporte(conn: sqlite3.Connection) -> None:
    """O perfil Suporte sai: quem o usava vira Administrador (já tinha acesso total, nada muda para a pessoa)."""
    conn.execute("UPDATE users SET role = 'admin' WHERE role = 'suporte'")
    conn.execute("DELETE FROM roles WHERE key = 'suporte'")


# Turmas de exemplo antigas -> padrão FIAP ({ano}EC{turno}; A manhã, T tarde, R noite).
FIAP_CLASS_MAP = {"1º A": "1ECA", "1º B": "2ECA", "2º A": "1ECT", "2º B": "2ECT", "3º C": "1ECR", "3º D": "2ECR"}


def _m14_padrao_fiap(conn: sqlite3.Connection) -> None:
    """
    Turmas e RMs no padrão FIAP, sem apagar nada:
    * turmas de exemplo "1º A"… viram "1ECA"… (aluno, turma e frequência do dia), se o nome novo estiver livre;
    * RM dos alunos fictícios (FIC001…) vira RM550001…;
    * RM real com só 6 números ("566217") ganha o prefixo: "RM566217".
    """
    for old, new in FIAP_CLASS_MAP.items():
        if not conn.execute("SELECT 1 FROM classes WHERE name = ?", (old,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM classes WHERE name = ? COLLATE NOCASE", (new,)).fetchone():
            continue
        conn.execute("UPDATE classes SET name = ? WHERE name = ?", (new, old))
        conn.execute("UPDATE students SET class_name = ? WHERE class_name = ?", (new, old))
        if "class_name" in _columns(conn, "attendance_days"):
            conn.execute("UPDATE attendance_days SET class_name = ? WHERE class_name = ?", (new, old))

    used = {r[0] for r in conn.execute("SELECT registration_number FROM students WHERE registration_number IS NOT NULL")}

    def rename(student_id: int, new_rm: str) -> None:
        conn.execute("UPDATE students SET registration_number = ? WHERE id = ?", (new_rm, student_id))
        conn.execute("UPDATE presences SET registration_number = ? WHERE student_id = ?", (new_rm, student_id))
        used.add(new_rm)

    for sid, rm in conn.execute("SELECT id, registration_number FROM students WHERE registration_number LIKE 'FIC%' "
                                "ORDER BY id").fetchall():
        digits = "".join(ch for ch in rm if ch.isdigit())
        k = 550000 + (int(digits) if digits else sid)
        while f"RM{k:06d}" in used:
            k += 1000
        rename(sid, f"RM{k:06d}")
    for sid, rm in conn.execute("SELECT id, registration_number FROM students").fetchall():
        raw = "".join(str(rm or "").split()).upper()
        new_rm = "RM" + raw if raw.isdigit() and len(raw) == 6 else raw
        if new_rm != rm and new_rm.startswith("RM") and len(new_rm) == 8 and new_rm[2:].isdigit() \
                and new_rm not in used:
            rename(sid, new_rm)


# (versão, nome, função). Para mudar o schema: acrescente uma NOVA entrada.
MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "schema base", _m1_base),
    (2, "índices", _m2_indices),
    (3, "acesso: usuários, perfis, sessões e auditoria", _m3_acesso),
    (4, "cadastro de câmeras", _m4_cameras),
    (5, "escola: turnos, turmas, calendário e presença do dia", _m5_escola),
    (6, "dados fictícios e configurações do sistema", _m6_ficticios_integracoes),
    (7, "vários responsáveis por aluno e detalhes da ocorrência", _m7_responsaveis_ocorrencias),
    (8, "bot de segurança: conta no Telegram e cartões de alerta", _m8_bot_seguranca),
    (9, "alertas de segurança individuais (por conta vinculada)", _m9_alertas_individuais),
    (10, "retiradas autorizadas pelos responsáveis e preferências de aviso", _m10_retiradas_preferencias),
    (11, "histórico de avisos aos responsáveis", _m11_historico_avisos),
    (12, "sem câmeras de exemplo", _m12_sem_cameras_exemplo),
    (13, "portaria e segurança num perfil só", _m13_portaria_e_seguranca),
    (14, "turmas e RM no padrão FIAP", _m14_padrao_fiap),
    (15, "sem o perfil Suporte", _m15_sem_perfil_suporte),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                 "version INTEGER PRIMARY KEY, name TEXT, "
                 "applied_at DATETIME DEFAULT (datetime('now','localtime')))")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection) -> int:
    """Aplica as migrações pendentes, em ordem. Devolve a versão final."""
    version = current_version(conn)
    conn.commit()
    for number, name, fn in MIGRATIONS:
        if number <= version:
            continue
        try:
            conn.execute("BEGIN")
            fn(conn)
            conn.execute("INSERT INTO schema_version (version, name) VALUES (?, ?)", (number, name))
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception(f"Falha na migração {number} ({name}). O banco ficou na versão {version}.")
            raise
        version = number
        logger.info(f"Banco migrado para a versão {number}: {name}.")
    return version


# ---------------------------------------------------------------- dados iniciais
def _seed_camera(conn: sqlite3.Connection, camera_id: int, name: str) -> None:
    """Garante a câmera do realguard.env no cadastro, SEM senha (ela fica só no .env)."""
    mode = os.getenv("CAMERA_MODE", "webcam").strip().lower()
    kind = "rtsp" if mode == "rtsp" or os.getenv("RTSP_URL", "").strip() else "webcam"
    def env_int(key: str, default: int) -> int:
        value = os.getenv(key, "").strip()
        return int(value) if value.isdigit() else default

    meta = {
        "source_kind": kind,
        "webcam_index": env_int("CAMERA_INDEX", 0),
        "host": os.getenv("CAMERA_IP", "").strip() if kind == "rtsp" else "",
        "port": env_int("CAMERA_PORT", 554),
        "path": os.getenv("CAMERA_RTSP_PATH", "/cam/realmonitor?channel=1&subtype=1") if kind == "rtsp" else "",
        "username": os.getenv("CAMERA_USER", "").strip() if kind == "rtsp" else "",
    }
    row = conn.execute("SELECT id, created_at FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO cameras (id, name, rtsp_url, enabled, location, source_kind, webcam_index, host, port, path, "
            "username, created_at) VALUES (?, ?, ?, 1, '', ?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
            (camera_id, name, "definida-em-config/realguard.env", meta["source_kind"], meta["webcam_index"],
             meta["host"], meta["port"], meta["path"], meta["username"]),
        )
    elif row[1] is None:
        # Câmera criada por versões antigas (só nome): completa uma vez com o que está no .env.
        conn.execute(
            "UPDATE cameras SET source_kind = ?, webcam_index = ?, host = ?, port = ?, path = ?, username = ?, "
            "created_at = datetime('now','localtime') WHERE id = ?",
            (meta["source_kind"], meta["webcam_index"], meta["host"], meta["port"], meta["path"],
             meta["username"], camera_id),
        )
    elif kind == "rtsp" and meta["host"] and not os.getenv("RTSP_URL", "").strip() \
            and str(camera_id) == (os.getenv("CAMERA_ID", "1").strip() or "1"):
        # O sistema liga na câmera do realguard.env (IP/caminho escolhidos no terminal): o cadastro
        # mostra o mesmo endereço, senão o dashboard exibiria (e salvaria de volta) o antigo.
        # SÓ a câmera do .env: ao trocar para outra câmera pelo dashboard, o motor reinicia com o id
        # dela e não pode copiar o endereço da padrão por cima do cadastro dela.
        conn.execute("UPDATE cameras SET source_kind = 'rtsp', host = ?, port = ?, path = ?, username = ? "
                     "WHERE id = ?", (meta["host"], meta["port"], meta["path"], meta["username"], camera_id))


def _import_legacy_telegram_contacts(conn: sqlite3.Connection) -> None:
    """Migra o antigo config/telegram_contacts.json (RM -> chat_id) para o banco."""
    if not os.path.isfile(LEGACY_TELEGRAM_JSON):
        return
    try:
        with open(LEGACY_TELEGRAM_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Não consegui ler {LEGACY_TELEGRAM_JSON}: {e}")
        return
    if not isinstance(data, dict):
        return
    for rm, chat_id in data.items():
        rm = str(rm).strip()
        with_prefix = "RM" + rm if rm.isdigit() else rm      # RM no padrão FIAP (migração 14)
        conn.execute(
            "UPDATE students SET telegram_chat_id = ? "
            "WHERE registration_number IN (?, ?) AND COALESCE(telegram_chat_id, '') = ''",
            (str(chat_id), rm, with_prefix),
        )


def initialize_database(camera_id: int = 1, camera_name: str = "Camera Principal") -> int:
    """Migra o banco até a última versão e garante os dados iniciais. Idempotente."""
    with get_db_connection() as conn:
        version = migrate(conn)
        _seed_camera(conn, camera_id, camera_name)
        _import_legacy_telegram_contacts(conn)
        # Perfis padrão (admin/gestor/operador) sempre sincronizados com o código.
        from database import access
        access.sync_system_roles(conn)
        # Feriados nacionais do ano corrente e do próximo (calculados sem internet).
        from database import school
        school.ensure_national_holidays(conn)
        conn.commit()
    return version
