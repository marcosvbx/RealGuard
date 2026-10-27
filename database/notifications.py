"""
Histórico de avisos aos responsáveis (Atividades recentes da Visão geral e Ocorrências): uma linha por mensagem
enviada a um responsável — automática (entrada/saída) ou pela portaria (ausência,
saída antecipada). O status começa PENDING e é atualizado quando o Telegram responde.
"""

import logging
from config.clock import date, datetime
from typing import Any, Dict, List, Optional

from database.connection import get_db_connection

logger = logging.getLogger(__name__)

PENDING, SENT, FAILED = "PENDING", "SENT", "FAILED"
KINDS = {"ENTRY": "Entrada", "LATE": "Atraso", "EXIT": "Saída", "ABSENCE": "Ausência", "EARLY_EXIT": "Saída antecipada"}


_TG_ERRORS = (
    ("bot was blocked by the user", "O responsável bloqueou o bot no Telegram"),
    ("user is deactivated", "A conta do Telegram do responsável foi desativada"),
    ("chat not found", "Conversa não encontrada: o responsável precisa abrir o bot e tocar em Iniciar"),
    ("bot can't initiate conversation", "O responsável ainda não tocou em Iniciar no bot"),
    ("have no rights to send", "O bot não tem permissão para escrever nessa conversa"),
    ("too many requests", "O Telegram pediu para esperar (muitas mensagens seguidas)"),
    ("unauthorized", "Token do bot inválido ou revogado (Escola → Integrações)"),
    ("falha de rede", "Sem internet no momento do envio"),
    ("timed out", "Sem internet no momento do envio"),
    ("token do telegram não configurado", "Bot dos responsáveis não configurado"),
)


def detail_pt(detail: str) -> str:
    """Erro do Telegram em português, para a tela (o texto original fica no banco)."""
    low = (detail or "").lower()
    for needle, text in _TG_ERRORS:
        if needle in low:
            return text
    return detail or ""


def guardian_label(student_id: Optional[int], chat_id: str) -> str:
    """'Nome (parentesco)' do responsável dono deste chat, para o histórico."""
    if not student_id or not chat_id:
        return ""
    try:
        with get_db_connection() as conn:
            r = conn.execute("SELECT g.name, sg.relationship FROM student_guardians sg JOIN guardians g "
                             "ON g.id = sg.guardian_id WHERE sg.student_id = ? AND g.telegram_chat_id = ?",
                             (int(student_id), str(chat_id))).fetchone()
    except Exception:
        return ""
    if not r:
        return ""
    return f"{r['name']} ({r['relationship']})" if r["relationship"] else r["name"]


def log(student_id: Optional[int], student_name: str, kind: str, chat_id: str = "", guardian: str = "",
        status: str = PENDING, detail: str = "", sent_by: str = "", at: Optional[datetime] = None) -> Optional[int]:
    """Registra um aviso. Nunca derruba o envio: erro aqui só vira log. `at`: horário (padrão: agora)."""
    try:
        guardian = guardian or guardian_label(student_id, chat_id)
        when = (at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            cur = conn.execute("INSERT INTO notification_log (student_id, student_name, guardian, chat_id, kind, "
                               "status, detail, sent_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (student_id, student_name or "", guardian, str(chat_id or ""), kind, status,
                                (detail or "")[:300], sent_by or "", when))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"Aviso não registrado no histórico: {e}")
        return None


def update(log_id: Optional[int], status: str, detail: str = "") -> None:
    if not log_id:
        return
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE notification_log SET status = ?, detail = ? WHERE id = ?",
                         (status, (detail or "")[:300], int(log_id)))
            conn.commit()
    except Exception as e:
        logger.warning(f"Histórico de aviso não atualizado: {e}")


def list_log(day: Optional[date] = None, kind: str = "", failed_only: bool = False, q: str = "",
             limit: int = 500) -> List[Dict[str, Any]]:
    day = day or date.today()
    sql, args = "SELECT * FROM notification_log WHERE date(created_at) = ?", [day.isoformat()]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if failed_only:
        sql += " AND status = ?"
        args.append(FAILED)
    if q:
        sql += " AND (student_name LIKE ? OR guardian LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        r["detail"] = detail_pt(r.get("detail") or "")
    return rows


def summary(day: Optional[date] = None) -> Dict[str, int]:
    day = day or date.today()
    with get_db_connection() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM notification_log WHERE date(created_at) = ? "
                            "GROUP BY status", (day.isoformat(),)).fetchall()
        guardians = conn.execute("SELECT COUNT(DISTINCT chat_id) FROM notification_log WHERE date(created_at) = ? "
                                 "AND chat_id != ''", (day.isoformat(),)).fetchone()[0]
    by = {r["status"]: r["n"] for r in rows}
    return {"total": sum(by.values()), "entregues": by.get(SENT, 0), "falharam": by.get(FAILED, 0),
            "na_fila": by.get(PENDING, 0), "responsaveis": guardians}


def get(log_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM notification_log WHERE id = ?", (int(log_id),)).fetchone()
    return dict(r) if r else None


def open_failures(day: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Falhas do dia que ainda pedem atenção: some quando o mesmo aviso (aluno, tipo, chat)
    é entregue depois — por reenvio da portaria ou por um envio automático seguinte.
    """
    day = (day or date.today()).isoformat()
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT f.* FROM notification_log f WHERE date(f.created_at) = ? AND f.status = ? AND NOT EXISTS ("
            " SELECT 1 FROM notification_log s WHERE date(s.created_at) = ? AND s.status = ? AND s.id > f.id"
            " AND s.kind = f.kind AND s.chat_id = f.chat_id AND IFNULL(s.student_id, 0) = IFNULL(f.student_id, 0)) "
            "ORDER BY f.id DESC LIMIT 100", (day, FAILED, day, SENT)).fetchall()]
        for r in rows:
            r["detail"] = detail_pt(r.get("detail") or "")
        return rows


def students_without_telegram(limit: int = 60) -> Dict[str, Any]:
    """Alunos sem nenhum responsável com Telegram vinculado (não recebem nenhum aviso)."""
    sql = ("FROM students s WHERE IFNULL(s.telegram_chat_id, '') = '' AND NOT EXISTS ("
           " SELECT 1 FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id"
           " WHERE sg.student_id = s.id AND IFNULL(g.telegram_chat_id, '') != '')")
    with get_db_connection() as conn:
        total = conn.execute("SELECT COUNT(*) " + sql).fetchone()[0]
        students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            "SELECT s.id, s.name, s.class_name, s.guardian_name " + sql + " ORDER BY s.class_name, s.name LIMIT ?",
            (int(limit),))]
    return {"total": total, "alunos": rows, "alunos_cadastrados": students}
