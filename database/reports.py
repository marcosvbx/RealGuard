"""
Consultas agregadas do dashboard (Visão geral, Ocorrências, Relatórios).

Fase 1: os números de presença vêm da presença CONSOLIDADA do dia
(database/attendance.py), que já considera turno, tolerância da turma,
calendário escolar e correções humanas. As passagens brutas da câmera
continuam alimentando só "movimento" (entradas/saídas por horário).
"""

import logging
from datetime import timedelta
from config.clock import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from database import attendance, school
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

NO_CLASS = "Sem turma"
PERIODS = {"today": 0, "week": 6, "month": 29}  # dias para trás a partir de hoje (compatibilidade)
# Períodos escolares: "semana" (desde segunda), "ano" (ano letivo), "p1".."p4" (bimestres/trimestres/semestres).
SCHOOL_PERIODS = ("semana", "ano", "p1", "p2", "p3", "p4")
MIN_FREQ = 75.0          # LDB: frequência mínima de 75%
WATCH_FREQ = 80.0        # faixa de atenção
STREAK_ALERT = 3         # faltas seguidas = busca ativa
MAX_RANGE_DAYS = 366


def _minutes(hhmm: str) -> int:
    h, m = (hhmm.split(":") + ["0"])[:2]
    return int(h) * 60 + int(m)


def _by_hour(times) -> List[Dict[str, int]]:
    counts: Dict[int, int] = {}
    for t in times:
        h = int(str(t)[:2])
        counts[h] = counts.get(h, 0) + 1
    return [{"h": h, "n": counts[h]} for h in sorted(counts)]


def _entry(row: Dict[str, Any]) -> Optional[str]:
    return row.get("manual_entry") or row.get("cam_entry")


# ================================================================== visão geral (hoje)
ACTIVITY_KINDS = ("ENTRY", "EXIT", "ABSENCE", "ALERT")


def recent_activity(limit: int = 30, tipo: str = "", turma: str = "", q: str = "",
                    day: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Atividades recentes da Visão geral, do mais recente para o mais antigo, já filtradas no banco
    (filtrar só as últimas N no navegador esconderia, por exemplo, as entradas da manhã de uma turma).

    tipo "" = passagens (entrada/saída) e alertas; "ENTRY" / "EXIT" / "ALERT"; "ABSENCE" = avisos de
    ausência aos responsáveis (uma linha por aluno e dia, mesmo com vários responsáveis avisados).
    """
    cal = school.Calendar.load()
    tipo = tipo if tipo in ACTIVITY_KINDS else ""
    like = f"%{q.strip()}%" if q and q.strip() else ""
    d = day.isoformat() if day else ""        # Visão geral: só o dia de hoje
    out: List[Dict[str, Any]] = []
    guardian_match = ("EXISTS (SELECT 1 FROM student_guardians sgq JOIN guardians gq ON gq.id = sgq.guardian_id "
                      "WHERE sgq.student_id = {sid} AND gq.name LIKE ?)")
    with get_db_connection() as conn:
        if tipo in ("", "ENTRY", "EXIT"):
            where, args = (["date(p.created_at) = ?"], [d]) if d else ([], [])
            if tipo:
                where.append("p.direction = ?")
                args.append(tipo)
            if turma:
                where.append("s.class_name = ?")
                args.append(turma)
            if like:
                where.append("(COALESCE(s.name, p.student_name) LIKE ? OR s.registration_number LIKE ? OR "
                             + guardian_match.format(sid="p.student_id") + ")")
                args += [like, like, like]
            passages = [dict(r) for r in conn.execute(
                "SELECT p.id, p.student_id, COALESCE(s.name, p.student_name) AS name, s.registration_number AS rm, "
                "s.class_name AS turma, p.direction, p.telegram_status, p.telegram_detail, p.created_at, "
                "(SELECT g.name FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id "
                " WHERE sg.student_id = p.student_id ORDER BY sg.is_primary DESC, sg.created_at LIMIT 1) AS g_name, "
                "(SELECT sg.relationship FROM student_guardians sg WHERE sg.student_id = p.student_id "
                " ORDER BY sg.is_primary DESC, sg.created_at LIMIT 1) AS g_rel "
                "FROM presences p LEFT JOIN students s ON s.id = p.student_id "
                + ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY p.id DESC LIMIT ?",
                args + [int(limit)])]
            for p in passages:
                hora = (p["created_at"] or "")[11:19]
                late = False
                if p["direction"] == "ENTRY" and p["student_id"]:
                    shift = cal.shift_for_class(p["turma"] or "")
                    late = bool(shift) and _minutes(hora[:5]) > _minutes(shift["deadline"])
                out.append({"tipo": p["direction"], "id": p["id"], "student_id": p["student_id"], "nome": p["name"] or "",
                            "rm": p["rm"] or "", "turma": p["turma"] or "", "hora": hora, "quando": p["created_at"],
                            "atraso": late, "aviso": p["telegram_status"] or "",
                            "aviso_detalhe": _tg_pt(p["telegram_detail"] or ""),
                            "responsavel": {"nome": p["g_name"], "parentesco": p["g_rel"] or ""} if p["g_name"] else None})
        if tipo in ("", "ALERT"):
            where, args = (["date(e.created_at) = ?"], [d]) if d else ([], [])
            if turma:
                where.append("s.class_name = ?")
                args.append(turma)
            if like:
                where.append("(s.name LIKE ? OR s.registration_number LIKE ?)")
                args += [like, like]
            for a in conn.execute(
                    "SELECT e.id, e.student_id, s.name, s.registration_number AS rm, s.class_name AS turma, e.event_type, "
                    "e.description, e.status, e.created_at FROM security_events e "
                    "LEFT JOIN students s ON s.id = e.student_id "
                    + ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY e.id DESC LIMIT ?",
                    args + [int(limit)]):
                out.append({"tipo": "ALERT", "id": a["id"], "student_id": a["student_id"], "nome": a["name"] or "",
                            "rm": a["rm"] or "", "turma": a["turma"] or "", "hora": (a["created_at"] or "")[11:19],
                            "quando": a["created_at"], "evento": a["event_type"], "descricao": a["description"] or "",
                            "situacao": a["status"], "aviso": ""})   # alerta vai para a equipe, não para os pais
        if tipo == "ABSENCE":
            where, args = ["n.kind = 'ABSENCE'"], []
            if d:
                where.append("date(n.created_at) = ?")
                args.append(d)
            if turma:
                where.append("s.class_name = ?")
                args.append(turma)
            if like:
                where.append("(COALESCE(s.name, n.student_name) LIKE ? OR s.registration_number LIKE ? OR n.guardian LIKE ?)")
                args += [like, like, like]
            rows = [dict(r) for r in conn.execute(
                "SELECT n.id, n.student_id, COALESCE(s.name, n.student_name) AS name, s.registration_number AS rm, "
                "s.class_name AS turma, n.guardian, n.status, n.detail, n.sent_by, n.created_at "
                "FROM notification_log n LEFT JOIN students s ON s.id = n.student_id WHERE " + " AND ".join(where)
                + " ORDER BY n.id DESC LIMIT ?", args + [int(limit) * 4])]
            seen: Dict[Tuple[Any, str], Dict[str, Any]] = {}
            for n in rows:   # um aviso por aluno e dia (cada responsável recebe o seu, mas é a mesma ausência)
                key = (n["student_id"], (n["created_at"] or "")[:10])
                g = (n["guardian"] or "").rsplit(" (", 1)
                if key in seen:
                    item = seen[key]
                    item["outros_responsaveis"] += 1
                    if n["status"] == "SENT":
                        item["aviso"] = "SENT"
                    continue
                item = {"tipo": "ABSENCE", "id": n["id"], "student_id": n["student_id"], "nome": n["name"] or "",
                        "rm": n["rm"] or "", "turma": n["turma"] or "", "hora": (n["created_at"] or "")[11:19],
                        "quando": n["created_at"], "aviso": n["status"] or "", "aviso_detalhe": _tg_pt(n["detail"] or ""),
                        "enviado_por": n["sent_by"] or "", "outros_responsaveis": 0,
                        "responsavel": {"nome": g[0], "parentesco": g[1].rstrip(")") if len(g) > 1 else ""} if g[0] else None}
                seen[key] = item
                out.append(item)
    out.sort(key=lambda x: x["quando"] or "", reverse=True)
    return out[:limit]


def activity_counts(turma: str = "", day: Optional[date] = None) -> Dict[str, int]:
    """Quantas entradas, saídas, ausências avisadas e alertas houve hoje (para os botões de filtro)."""
    d = (day or date.today()).isoformat()
    cls, args = (" AND s.class_name = ?", [turma]) if turma else ("", [])
    with get_db_connection() as conn:
        dirs = {r[0]: r[1] for r in conn.execute(
            "SELECT p.direction, COUNT(*) FROM presences p LEFT JOIN students s ON s.id = p.student_id "
            "WHERE date(p.created_at) = ?" + cls + " GROUP BY p.direction", [d] + args)}
        absn = conn.execute("SELECT COUNT(DISTINCT n.student_id) FROM notification_log n LEFT JOIN students s "
                            "ON s.id = n.student_id WHERE n.kind = 'ABSENCE' AND date(n.created_at) = ?" + cls,
                            [d] + args).fetchone()[0]
        alerts = conn.execute("SELECT COUNT(*) FROM security_events e LEFT JOIN students s ON s.id = e.student_id "
                              "WHERE date(e.created_at) = ?" + cls, [d] + args).fetchone()[0]
    return {"ENTRY": dirs.get("ENTRY", 0), "EXIT": dirs.get("EXIT", 0), "ABSENCE": absn, "ALERT": alerts}


def _tg_pt(detail: str) -> str:
    from database.notifications import detail_pt
    return detail_pt(detail)


def _absence_minutes() -> int:
    try:
        return max(0, int(school.get_settings().get("absence_notice_after_minutes", 60)))
    except (TypeError, ValueError):
        return 60


def get_overview(tolerance: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now()
    today = now.date()
    cal = school.Calendar.load()
    attendance.close_pending_days(now)
    rows = attendance.consolidate_day(today, now=now, cal=cal)
    with get_db_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM security_events WHERE status='PENDING'").fetchone()[0]
        recent = [dict(r) for r in conn.execute(
            "SELECT id, student_name, direction, telegram_status, created_at FROM presences ORDER BY id DESC LIMIT 8")]
        last_dir: Dict[int, str] = {}
        exited = set()
        for r in conn.execute("SELECT student_id, direction FROM presences WHERE student_id IS NOT NULL "
                              "AND date(created_at) = ? ORDER BY id", (today.isoformat(),)):
            last_dir[r["student_id"]] = r["direction"]
            if r["direction"] == "EXIT":
                exited.add(r["student_id"])

    present = [r for r in rows if r["status"] in attendance.PRESENT_SET]
    turmas: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        name = r["class_name"] or NO_CLASS
        t = turmas.setdefault(name, {"turma": name, "turno": r.get("shift_name") or "", "total": 0, "presentes": 0,
                                     "atrasos": 0, "ausentes": 0, "aguardando": 0, "tolerancia": r["deadline"]})
        if r["status"] == attendance.JUSTIFICADA:
            continue   # falta justificada não entra na conta da turma
        t["total"] += 1
        t["presentes"] += 1 if r["status"] in attendance.PRESENT_SET else 0
        t["atrasos"] += 1 if r["status"] == attendance.ATRASADO else 0
        t["ausentes"] += 1 if r["status"] == attendance.AUSENTE else 0
        t["aguardando"] += 1 if r["status"] == attendance.PENDENTE else 0   # turno ainda no horário de entrada

    now_min = now.hour * 60 + now.minute
    deadlines = sorted({r["deadline"] for r in rows})
    upcoming = [d for d in deadlines if _minutes(d) >= now_min]
    is_school, reason = (bool(rows), "") if rows else cal.day_status(today, cal.default_shift)
    if not rows and is_school:   # dia letivo, mas nenhum aluno com turno hoje
        reason = "Nenhum aluno com aula hoje"
    shifts_today = {}
    for r in rows:
        shifts_today.setdefault(r.get("shift_name") or "", r["deadline"])
    absent = [{"id": r["student_id"], "name": r["name"] or "(removido)", "class_name": r["class_name"] or NO_CLASS,
               "telegram_chat_id": r["telegram_chat_id"], "turno": r.get("shift_name") or "",
               "tolerancia": r["deadline"]}
              for r in rows if r["status"] == attendance.AUSENTE]
    early = [{"id": r["student_id"], "name": r["name"] or "(removido)", "class_name": r["class_name"] or NO_CLASS,
              "telegram_chat_id": r["telegram_chat_id"], "turno": r.get("shift_name") or "",
              "saida": r["cam_exit"], "fim_turno": r["shift_end"]}
             for r in rows if r["early_exit"]]
    return {
        # horário de referência mostrado na tela: a próxima tolerância do dia (ou a última, se já passaram todas)
        "tolerancia": (upcoming[0] if upcoming else deadlines[-1]) if deadlines else tolerance,
        "tolerancias": [{"turno": k, "tolerancia": v} for k, v in sorted(shifts_today.items(), key=lambda x: x[1])],
        "agora": now.strftime("%H:%M"), "dia": today.isoformat(),
        "passou_tolerancia": any(_minutes(d) < now_min for d in deadlines),
        "dia_letivo": bool(rows), "motivo_sem_aula": "" if rows else reason,
        "cadastrados": total, "esperados": len([r for r in rows if r["status"] != attendance.JUSTIFICADA]),
        "presentes": len(present),
        "atrasos": sum(1 for r in rows if r["status"] == attendance.ATRASADO),
        "justificadas": sum(1 for r in rows if r["status"] == attendance.JUSTIFICADA),
        "ausentes": absent, "aviso_ausencia_min": _absence_minutes(), "turmas": sorted(turmas.values(), key=lambda t: t["turma"]),
        "dentro": sum(1 for d in last_dir.values() if d == "ENTRY"), "saidas": len(exited),
        "saidas_antecipadas": len(early), "saidas_antecipadas_lista": early,
        "alertas": pending, "ultimas": recent, "atividades": recent_activity(30, day=today),
        # 1ª chegada de cada aluno hoje, por hora (gráfico da Visão geral)
        "chegadas_hora": _by_hour(_entry(r) for r in present),
        # a mesma coisa por turma (clicar numa turma na Visão geral filtra o gráfico)
        "chegadas_hora_turma": {name: _by_hour(_entry(r) for r in present if (r["class_name"] or NO_CLASS) == name)
                                for name in turmas},
    }


# ================================================================== relatórios
def resolve_range(period: str = "week", start: str = "", end: str = "",
                  today: Optional[date] = None) -> Tuple[date, date]:
    today = today or date.today()
    if start or end:
        s = school.parse_day(start or end)
        e = school.parse_day(end or start)
        if e < s:
            s, e = e, s
        e = min(e, today)
        s = max(s, e - timedelta(days=MAX_RANGE_DAYS - 1))
        return s, e
    if period == "semana":
        return today - timedelta(days=today.weekday()), today
    if period == "ano" or (period or "").startswith("p"):
        info = school.academic_periods(today)
        if period == "ano":
            a, b = school.parse_day(info["ano"]["inicio"]), school.parse_day(info["ano"]["fim"])
        else:
            p = next((x for x in info["periodos"] if x["chave"] == period), None)
            if p is None:
                return today - timedelta(days=6), today
            a, b = school.parse_day(p["inicio"]), school.parse_day(p["fim"])
        b = min(b, today)
        if a > b:          # período que ainda não começou: vazio (início = fim = início do período)
            return a, a
        return a, b
    return today - timedelta(days=PERIODS.get(period, 6)), today


def _filtered_rows(start: date, end: date, shift_id: Optional[int], class_name: str,
                   student_id: Optional[int]) -> List[Dict[str, Any]]:
    sql = ("SELECT a.*, s.name, s.registration_number, sh.name AS shift_name FROM attendance_days a "
           "LEFT JOIN students s ON s.id = a.student_id LEFT JOIN shifts sh ON sh.id = a.shift_id "
           "WHERE a.day BETWEEN ? AND ?")
    params: list = [start.isoformat(), end.isoformat()]
    if shift_id:
        sql += " AND a.shift_id = ?"
        params.append(int(shift_id))
    if class_name:
        sql += " AND a.class_name = ? COLLATE NOCASE"
        params.append(class_name)
    if student_id:
        sql += " AND a.student_id = ?"
        params.append(int(student_id))
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY a.day, s.name COLLATE NOCASE", params)]


def get_report(period: str, tolerance: str = "", start: str = "", end: str = "", shift_id: Optional[int] = None,
               class_name: str = "", student_id: Optional[int] = None) -> Dict[str, Any]:
    s, e = resolve_range(period, start, end)
    attendance.ensure_range(s, e)
    rows = [r for r in _filtered_rows(s, e, shift_id, class_name, student_id) if r["status"] != attendance.PENDENTE]
    ids = {r["student_id"] for r in rows}

    with get_db_connection() as conn:
        sql = ("SELECT CAST(strftime('%H', p.created_at) AS INTEGER) AS h, COUNT(*) AS n FROM presences p "
               "LEFT JOIN students st ON st.id = p.student_id WHERE date(p.created_at) BETWEEN ? AND ?")
        params: list = [s.isoformat(), e.isoformat()]
        if class_name or shift_id or student_id:
            if not ids:
                sql += " AND 0"
            else:
                sql += f" AND p.student_id IN ({','.join('?' for _ in ids)})"
                params += list(ids)
        por_hora = [dict(r) for r in conn.execute(sql + " GROUP BY h ORDER BY h", params)]
        passagens = sum(r["n"] for r in por_hora)

    present = [r for r in rows if r["status"] in attendance.PRESENT_SET]
    late = [r for r in rows if r["status"] == attendance.ATRASADO]
    absent = [r for r in rows if r["status"] == attendance.AUSENTE]
    justified = [r for r in rows if r["status"] == attendance.JUSTIFICADA]
    expected = len(rows) - len(justified)

    por_dia: Dict[str, Dict[str, Any]] = {}
    turmas: Dict[str, Dict[str, Any]] = {}
    per_student: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        d = por_dia.setdefault(r["day"], {"d": r["day"], "n": 0, "atrasos": 0, "ausentes": 0})
        t = turmas.setdefault(r["class_name"] or NO_CLASS, {"turma": r["class_name"] or NO_CLASS,
                                                           "turno": r.get("shift_name") or "", "dias": 0,
                                                           "esperados": 0, "atrasos": 0, "min_atraso": 0})
        ps = per_student.setdefault(r["student_id"], {"dias": 0, "atrasos": 0, "min": 0, "faltas": 0,
                                                      "name": r["name"] or "(removido)",
                                                      "turma": r["class_name"] or NO_CLASS})
        if r["status"] != attendance.JUSTIFICADA:
            t["esperados"] += 1
        if r["status"] in attendance.PRESENT_SET:
            d["n"] += 1
            t["dias"] += 1
            ps["dias"] += 1
        if r["status"] == attendance.ATRASADO:
            mins = max(0, _minutes(_entry(r)) - _minutes(r["deadline"]))
            d["atrasos"] += 1
            t["atrasos"] += 1
            t["min_atraso"] += mins
            ps["atrasos"] += 1
            ps["min"] += mins
        if r["status"] == attendance.AUSENTE:
            d["ausentes"] += 1
            ps["faltas"] += 1

    por_turma = [{
        "turma": t["turma"], "turno": t["turno"], "dias": t["dias"], "esperados": t["esperados"], "atrasos": t["atrasos"],
        "frequencia_pct": round(100 * t["dias"] / t["esperados"], 1) if t["esperados"] else 0,
        "pontuais_pct": round(100 * (t["dias"] - t["atrasos"]) / t["dias"], 1) if t["dias"] else 0,
        "media_atraso_min": round(t["min_atraso"] / t["atrasos"], 1) if t["atrasos"] else 0,
    } for t in sorted(turmas.values(), key=lambda x: x["turma"])]

    with get_db_connection() as conn:
        sql = "SELECT COUNT(*) FROM students WHERE 1 = 1"
        params = []
        if class_name:
            sql += " AND class_name = ? COLLATE NOCASE"
            params.append(class_name)
        if student_id:
            sql += " AND id = ?"
            params.append(int(student_id))
        total = conn.execute(sql, params).fetchone()[0]
    if shift_id and not class_name and not student_id:
        cal = school.Calendar.load()
        with get_db_connection() as conn:
            total = sum(1 for r in conn.execute("SELECT class_name FROM students")
                        if (cal.shift_for_class(r[0]) or {}).get("id") == int(shift_id))

    present_ids = {r["student_id"] for r in present}
    extra = _insights(rows, s, e, shift_id, class_name, student_id, ids)
    return {**extra,
        "periodo": period, "inicio": s.isoformat(), "fim": e.isoformat(), "tolerancia": tolerance,
        "cadastrados": total, "passagens": passagens, "alunos_presentes": len(present_ids),
        "dias_letivos": len(por_dia),
        # frequência: presenças / dias esperados (falta justificada não conta contra)
        "assiduidade": round(100 * len(present) / expected, 1) if expected else 0,
        "presencas": len(present), "esperados": expected,
        "atrasos": len(late), "faltas": len(absent), "justificadas": len(justified),
        "saidas_antecipadas": sum(r["early_exit"] for r in rows),
        "por_hora": por_hora, "por_dia": sorted(por_dia.values(), key=lambda x: x["d"]), "por_turma": por_turma,
        "top_atrasos": sorted(
            [{"id": sid, "name": v["name"], "turma": v["turma"], "dias": v["dias"], "atrasos": v["atrasos"],
              "media_min": round(v["min"] / v["atrasos"], 1)} for sid, v in per_student.items() if v["atrasos"]],
            key=lambda x: (-x["atrasos"], -x["media_min"]))[:8],
        "top_faltas": sorted(
            [{"id": sid, "name": v["name"], "turma": v["turma"], "faltas": v["faltas"], "dias": v["dias"]}
             for sid, v in per_student.items() if v["faltas"]],
            key=lambda x: (-x["faltas"], x["name"]))[:8],
        # Cadastrados que não apareceram nenhuma vez no período
        "nunca_vistos": max(0, total - len(present_ids)),
    }


def _pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def _insights(rows: List[Dict[str, Any]], s: date, e: date, shift_id, class_name, student_id, ids) -> Dict[str, Any]:
    """Indicadores que dão valor à escola a partir do que já é coletado."""
    # --- frequência por aluno: abaixo de 75% (LDB) e faixa de atenção
    per: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        p = per.setdefault(r["student_id"], {"id": r["student_id"], "name": r["name"] or "(removido)",
                                             "turma": r["class_name"] or NO_CLASS, "esperados": 0, "presentes": 0,
                                             "dias": [], "saidas": 0})
        if r["status"] != attendance.JUSTIFICADA:
            p["esperados"] += 1
            p["dias"].append((r["day"], r["status"]))
        if r["status"] in attendance.PRESENT_SET:
            p["presentes"] += 1
        p["saidas"] += 1 if r["early_exit"] else 0
    risk, streaks = [], []
    for p in per.values():
        freq = _pct(p["presentes"], p["esperados"])
        faltas = p["esperados"] - p["presentes"]
        if p["esperados"] >= 10 and freq < WATCH_FREQ:     # período curto demais não diz nada
            risk.append({"id": p["id"], "name": p["name"], "turma": p["turma"], "frequencia_pct": freq,
                         "faltas": faltas, "esperados": p["esperados"],
                         "nivel": "abaixo" if freq < MIN_FREQ else "atencao"})
        # faltas seguidas: as que terminam no último dia do período (ainda em aberto) e a maior sequência
        cur = best = 0
        for _, st in sorted(p["dias"]):
            if st == attendance.AUSENTE:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        if cur >= STREAK_ALERT or best >= STREAK_ALERT:
            streaks.append({"id": p["id"], "name": p["name"], "turma": p["turma"], "seguidas_agora": cur,
                            "maior_sequencia": best})
    risk.sort(key=lambda x: (x["frequencia_pct"], x["name"]))
    streaks.sort(key=lambda x: (-x["seguidas_agora"], -x["maior_sequencia"], x["name"]))

    # --- tendência semana a semana
    weeks: Dict[str, Dict[str, int]] = {}
    for r in rows:
        d = datetime.strptime(r["day"], "%Y-%m-%d").date()
        key = (d - timedelta(days=d.weekday())).isoformat()
        w = weeks.setdefault(key, {"esperados": 0, "presentes": 0, "atrasos": 0})
        if r["status"] != attendance.JUSTIFICADA:
            w["esperados"] += 1
        if r["status"] in attendance.PRESENT_SET:
            w["presentes"] += 1
        if r["status"] == attendance.ATRASADO:
            w["atrasos"] += 1
    tendencia = [{"semana": k, "frequencia_pct": _pct(v["presentes"], v["esperados"]),
                  "pontualidade_pct": _pct(v["presentes"] - v["atrasos"], v["presentes"])}
                 for k, v in sorted(weeks.items())]

    # --- dia da semana
    dias: Dict[int, Dict[str, int]] = {}
    for r in rows:
        wd = datetime.strptime(r["day"], "%Y-%m-%d").weekday()
        x = dias.setdefault(wd, {"esperados": 0, "presentes": 0, "atrasos": 0})
        if r["status"] != attendance.JUSTIFICADA:
            x["esperados"] += 1
        if r["status"] in attendance.PRESENT_SET:
            x["presentes"] += 1
        if r["status"] == attendance.ATRASADO:
            x["atrasos"] += 1
    dia_semana = [{"dia": school.WEEKDAYS[k][:3], "frequencia_pct": _pct(v["presentes"], v["esperados"]),
                   "atrasos_pct": _pct(v["atrasos"], v["presentes"])} for k, v in sorted(dias.items())]

    saidas = sorted([{"id": p["id"], "name": p["name"], "turma": p["turma"], "saidas": p["saidas"]}
                     for p in per.values() if p["saidas"] >= 2], key=lambda x: (-x["saidas"], x["name"]))[:8]

    # --- comparação com o período anterior de mesmo tamanho
    comp = None
    length = (e - s).days + 1
    ps, pe = s - timedelta(days=length), s - timedelta(days=1)
    try:
        prev = [r for r in _filtered_rows(ps, pe, shift_id, class_name, student_id)
                if r["status"] != attendance.PENDENTE]
        if prev:
            pj = sum(1 for r in prev if r["status"] == attendance.JUSTIFICADA)
            pp = sum(1 for r in prev if r["status"] in attendance.PRESENT_SET)
            pl = sum(1 for r in prev if r["status"] == attendance.ATRASADO)
            comp = {"inicio": ps.isoformat(), "fim": pe.isoformat(),
                    "assiduidade": _pct(pp, len(prev) - pj), "pontualidade": _pct(pp - pl, pp),
                    "faltas": sum(1 for r in prev if r["status"] == attendance.AUSENTE)}
    except Exception as ex:
        logger.warning(f"Comparativo indisponível: {ex}")

    # --- movimento por horário: entradas x saídas (linhas)
    with get_db_connection() as conn:
        sql = ("SELECT CAST(strftime('%H', created_at) AS INTEGER) AS h, "
               "SUM(direction = 'ENTRY') AS entradas, SUM(direction = 'EXIT') AS saidas FROM presences "
               "WHERE date(created_at) BETWEEN ? AND ?")
        params: list = [s.isoformat(), e.isoformat()]
        if class_name or shift_id or student_id:
            sql += (f" AND student_id IN ({','.join('?' for _ in ids)})" if ids else " AND 0")
            params += list(ids)
        mov = [dict(r) for r in conn.execute(sql + " GROUP BY h ORDER BY h", params)]
        # ocorrências de segurança (não dependem de turma)
        ev = [dict(r) for r in conn.execute(
            "SELECT status, category, created_at, validated_at FROM security_events "
            "WHERE date(created_at) BETWEEN ? AND ?", (s.isoformat(), e.isoformat()))]
    if mov:
        hs = range(min(m["h"] for m in mov), max(m["h"] for m in mov) + 1)
        got = {m["h"]: m for m in mov}
        mov = [{"h": h, "entradas": int((got.get(h) or {}).get("entradas") or 0),
                "saidas": int((got.get(h) or {}).get("saidas") or 0)} for h in hs]
    from app.notifications.messages import CATEGORIES
    confirmed = [x for x in ev if x["status"] == "VALIDATED_TRUE"]
    tipos: Dict[str, int] = {}
    for x in confirmed:
        k = x.get("category") or ""
        tipos[k] = tipos.get(k, 0) + 1
    por_hora: Dict[int, Dict[str, int]] = {}
    for x in ev:
        try:
            h = int(str(x["created_at"])[11:13])
        except ValueError:
            continue
        slot = por_hora.setdefault(h, {"confirmadas": 0, "outras": 0})
        slot["confirmadas" if x["status"] == "VALIDATED_TRUE" else "outras"] += 1
    resp = []
    for x in ev:
        if x["status"].startswith("VALIDATED") and x.get("validated_at"):
            try:
                resp.append((datetime.strptime(x["validated_at"][:19], "%Y-%m-%d %H:%M:%S")
                             - datetime.strptime(x["created_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60)
            except ValueError:
                pass
    ocorrencias = {"total": len(ev), "confirmadas": len(confirmed),
                   "descartadas": sum(1 for x in ev if x["status"] in ("VALIDATED_FALSE", "AUTO_DISMISSED")),
                   "descartadas_portaria": sum(1 for x in ev if x["status"] == "VALIDATED_FALSE"),
                   "descartadas_auto": sum(1 for x in ev if x["status"] == "AUTO_DISMISSED"),
                   "pendentes": sum(1 for x in ev if x["status"] == "PENDING"),
                   # tipo: chave da categoria ('' = confirmada antes de existir o campo "tipo")
                   "por_tipo": [{"chave": k, "tipo": CATEGORIES.get(k, "Sem tipo (antes desta versão)") if k else
                                 "Sem tipo (antes desta versão)", "n": v}
                                for k, v in sorted(tipos.items(), key=lambda t: (-t[1], t[0] == ""))],
                   "por_hora": ([{"h": h, **por_hora.get(h, {"confirmadas": 0, "outras": 0})}
                                 for h in range(min(por_hora), max(por_hora) + 1)] if por_hora else []),
                   "resposta_media_min": round(sum(resp) / len(resp), 1) if resp else None}
    return {"risco": risk[:30], "abaixo_75": sum(1 for r in risk if r["nivel"] == "abaixo"),
            "atencao_80": sum(1 for r in risk if r["nivel"] == "atencao"), "faltas_seguidas": streaks[:15],
            "tendencia": tendencia, "dia_semana": dia_semana, "saidas_recorrentes": saidas,
            "comparativo": comp, "movimento": mov, "ocorrencias": ocorrencias}


def export_rows(period: str, tolerance: str = "", start: str = "", end: str = "", shift_id: Optional[int] = None,
                class_name: str = "", student_id: Optional[int] = None) -> List[List[Any]]:
    """Linhas do CSV: uma por aluno por dia letivo, com a situação consolidada."""
    s, e = resolve_range(period, start, end)
    attendance.ensure_range(s, e)
    out = [["data", "rm", "aluno", "turma", "turno", "situacao", "entrada", "saida", "saida_antecipada",
            "ajuste_manual", "justificativa"]]
    for r in _filtered_rows(s, e, shift_id, class_name, student_id):
        if r["status"] == attendance.PENDENTE:
            continue
        out.append([r["day"], r["registration_number"] or "", r["name"] or "(removido)", r["class_name"] or NO_CLASS,
                    r.get("shift_name") or "", attendance.LABELS.get(r["status"], r["status"]).lower(),
                    _entry(r) or "", r["cam_exit"] or "", "sim" if r["early_exit"] else "",
                    f"{r['manual_by_name']} em {str(r['manual_at'])[:16]}" if r["manual_at"] else "",
                    r["manual_reason"] if r["manual_status"] == attendance.JUSTIFICADA else ""])
    return out
