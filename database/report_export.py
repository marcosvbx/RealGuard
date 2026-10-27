"""
Exportação personalizada dos relatórios (Relatórios → Exportar).

Antes de gerar o arquivo, a tela pergunta PARA QUE ele serve (um modelo pronto) e deixa ajustar as
colunas e os filtros. Quatro formatos de linha:

  frequencia  uma linha por aluno no período (boletim de frequência, busca ativa)
  diario      uma linha por aluno por dia letivo (registro completo, só faltas e atrasos)
  turmas      uma linha por turma (comparar turmas)
  dias        uma linha por dia (acompanhar o período)

Os números são os mesmos da tela (presença consolidada do dia: database/attendance.py).
Frequência = presenças ÷ dias esperados; falta justificada não conta contra.
"""

from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from database import attendance, school
from database.connection import get_db_connection
from database.reports import MIN_FREQ, NO_CLASS, WATCH_FREQ, resolve_range

SAIDA = "SAIDA_ANTECIPADA"          # filtro do diário: dias com saída antecipada (qualquer situação)
SITUATIONS = {"abaixo": "Abaixo de 75%", "atencao": "Atenção (75% a 80%)", "regular": "Regular",
              "sem": "Sem dias letivos"}

# (chave, título, padrão, precisa ver contatos, tipo) — tipo: text | int | num1
MODELS: Dict[str, Dict[str, Any]] = {
    "frequencia": {
        "nome": "Uma linha por aluno",
        "colunas": [
            ("rm", "RM", True, False, "text"), ("aluno", "Aluno", True, False, "text"),
            ("turma", "Turma", True, False, "text"), ("turno", "Turno", True, False, "text"),
            ("dias_letivos", "Dias letivos", True, False, "int"),
            ("esperados", "Presenças esperadas", False, False, "int"),
            ("presencas", "Presenças", True, False, "int"), ("atrasos", "Atrasos", True, False, "int"),
            ("faltas", "Faltas", True, False, "int"), ("justificadas", "Faltas justificadas", True, False, "int"),
            ("saidas", "Saídas antecipadas", False, False, "int"),
            ("frequencia", "Frequência (%)", True, False, "num1"),
            ("pontualidade", "Pontualidade (%)", False, False, "num1"),
            ("media_atraso", "Atraso médio (min)", False, False, "num1"),
            ("maior_sequencia", "Maior sequência de faltas", False, False, "int"),
            ("sequencia_atual", "Faltas seguidas até o fim do período", False, False, "int"),
            ("ultima_presenca", "Última presença", False, False, "text"),
            ("situacao", "Situação", True, False, "text"),
            ("responsavel", "Responsável principal", False, True, "text"),
            ("parentesco", "Parentesco", False, True, "text"), ("celular", "Celular", False, True, "text"),
            ("telegram", "Telegram", False, True, "text"),
        ],
    },
    "diario": {
        "nome": "Uma linha por aluno por dia",
        "colunas": [
            ("data", "Data", True, False, "text"), ("dia_semana", "Dia da semana", False, False, "text"),
            ("rm", "RM", True, False, "text"), ("aluno", "Aluno", True, False, "text"),
            ("turma", "Turma", True, False, "text"), ("turno", "Turno", True, False, "text"),
            ("situacao", "Situação", True, False, "text"), ("limite", "Limite de entrada", False, False, "text"),
            ("entrada", "Entrada", True, False, "text"), ("saida", "Saída", True, False, "text"),
            ("min_atraso", "Minutos de atraso", False, False, "int"),
            ("saida_antecipada", "Saída antecipada", True, False, "text"),
            ("ajuste", "Ajuste manual", True, False, "text"), ("justificativa", "Justificativa", True, False, "text"),
            ("responsavel", "Responsável principal", False, True, "text"), ("celular", "Celular", False, True, "text"),
        ],
    },
    "turmas": {
        "nome": "Uma linha por turma",
        "colunas": [
            ("turma", "Turma", True, False, "text"), ("turno", "Turno", True, False, "text"),
            ("alunos", "Alunos", True, False, "int"), ("dias_letivos", "Dias letivos", True, False, "int"),
            ("esperados", "Presenças esperadas", True, False, "int"), ("presencas", "Presenças", True, False, "int"),
            ("frequencia", "Frequência (%)", True, False, "num1"), ("atrasos", "Atrasos", True, False, "int"),
            ("pontualidade", "Pontualidade (%)", True, False, "num1"),
            ("media_atraso", "Atraso médio (min)", False, False, "num1"),
            ("faltas", "Faltas", True, False, "int"), ("justificadas", "Faltas justificadas", True, False, "int"),
            ("saidas", "Saídas antecipadas", False, False, "int"),
            ("abaixo", "Alunos abaixo de 75%", True, False, "int"), ("atencao", "Alunos em atenção", True, False, "int"),
        ],
    },
    "dias": {
        "nome": "Uma linha por dia",
        "colunas": [
            ("data", "Data", True, False, "text"), ("dia_semana", "Dia da semana", True, False, "text"),
            ("alunos", "Alunos no dia", True, False, "int"), ("presencas", "Presentes", True, False, "int"),
            ("atrasos", "Atrasos", True, False, "int"), ("faltas", "Faltas", True, False, "int"),
            ("justificadas", "Faltas justificadas", True, False, "int"),
            ("saidas", "Saídas antecipadas", False, False, "int"),
            ("frequencia", "Frequência (%)", True, False, "num1"),
        ],
    },
}

# Modelos prontos ("Para que é o arquivo?"). O usuário ainda pode trocar colunas e filtros.
PRESETS: List[Dict[str, Any]] = [
    {"chave": "frequencia", "nome": "Frequência por aluno", "modelo": "frequencia",
     "descricao": "Boletim de frequência: presenças, faltas, atrasos e % de cada aluno no período."},
    {"chave": "busca_ativa", "nome": "Busca ativa", "modelo": "frequencia", "situacao": "risco", "ordem": "frequencia",
     "descricao": "Só alunos abaixo de 75% ou em atenção, do menor para o maior, com o contato do responsável.",
     "colunas": ["rm", "aluno", "turma", "frequencia", "faltas", "sequencia_atual", "maior_sequencia",
                 "ultima_presenca", "situacao", "responsavel", "parentesco", "celular", "telegram"]},
    {"chave": "ocorrencias", "nome": "Faltas e atrasos", "modelo": "diario",
     "status": [attendance.ATRASADO, attendance.AUSENTE, attendance.JUSTIFICADA, SAIDA],
     "descricao": "Só os dias com falta, atraso, falta justificada ou saída antecipada."},
    {"chave": "diario", "nome": "Registro diário completo", "modelo": "diario",
     "descricao": "Todos os alunos em todos os dias letivos, com entrada, saída e ajustes (o CSV de antes)."},
    {"chave": "turmas", "nome": "Resumo por turma", "modelo": "turmas",
     "descricao": "Uma linha por turma para comparar frequência, pontualidade e alunos em risco."},
    {"chave": "dias", "nome": "Resumo por dia", "modelo": "dias",
     "descricao": "Uma linha por dia letivo: quantos vieram, faltaram e chegaram atrasados."},
]


class ExportError(ValueError):
    pass


def catalog(with_contacts: bool) -> Dict[str, Any]:
    models = {k: {"nome": m["nome"], "colunas": [
        {"chave": c[0], "titulo": c[1], "padrao": c[2], "contato": c[3]}
        for c in m["colunas"] if with_contacts or not c[3]]} for k, m in MODELS.items()}
    presets = []
    for p in PRESETS:
        p = dict(p)
        if p.get("colunas"):
            p["colunas"] = [c for c in p["colunas"] if any(x["chave"] == c for x in models[p["modelo"]]["colunas"])]
        presets.append(p)
    status = [{"chave": k, "nome": attendance.LABELS[k]} for k in (attendance.PRESENTE, attendance.ATRASADO,
                                                                    attendance.AUSENTE, attendance.JUSTIFICADA)]
    status.append({"chave": SAIDA, "nome": "Saída antecipada"})
    return {"modelos": models, "prontos": presets, "status": status,
            "situacoes": [{"chave": "todos", "nome": "Todos os alunos"},
                          {"chave": "risco", "nome": "Abaixo de 75% ou em atenção"},
                          {"chave": "abaixo", "nome": SITUATIONS["abaixo"]},
                          {"chave": "atencao", "nome": SITUATIONS["atencao"]},
                          {"chave": "regular", "nome": "Só regulares (80% ou mais)"}],
            "ordens": [{"chave": "turma", "nome": "Turma e nome"}, {"chave": "nome", "nome": "Nome"},
                       {"chave": "frequencia", "nome": "Menor frequência primeiro"},
                       {"chave": "faltas", "nome": "Mais faltas primeiro"}]}


# ================================================================== dados
def _minutes(hhmm: str) -> int:
    try:
        h, m = (str(hhmm).split(":") + ["0"])[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return 0


def _pct(a: int, b: int) -> Optional[float]:
    return round(100 * a / b, 1) if b else None


def _situation(freq: Optional[float]) -> str:
    if freq is None:
        return "sem"
    if freq < MIN_FREQ:
        return "abaixo"
    if freq < WATCH_FREQ:
        return "atencao"
    return "regular"


def _rows(s: date, e: date, shift_id: Optional[int], class_name: str, student_id: Optional[int],
          with_sample: bool) -> List[Dict[str, Any]]:
    sql = ("SELECT a.*, st.name, st.registration_number, st.ficticio, sh.name AS shift_name FROM attendance_days a "
           "LEFT JOIN students st ON st.id = a.student_id LEFT JOIN shifts sh ON sh.id = a.shift_id "
           "WHERE a.day BETWEEN ? AND ? AND a.status != ?")
    params: list = [s.isoformat(), e.isoformat(), attendance.PENDENTE]
    if shift_id:
        sql += " AND a.shift_id = ?"
        params.append(int(shift_id))
    if class_name:
        sql += " AND a.class_name = ? COLLATE NOCASE"
        params.append(class_name)
    if student_id:
        sql += " AND a.student_id = ?"
        params.append(int(student_id))
    if not with_sample:
        sql += " AND COALESCE(st.ficticio, 0) = 0"
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY a.day, st.name COLLATE NOCASE", params)]


def _contacts(student_ids) -> Dict[int, Dict[str, str]]:
    ids = [int(i) for i in student_ids if i]
    if not ids:
        return {}
    out: Dict[int, Dict[str, str]] = {}
    with get_db_connection() as conn:
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            for r in conn.execute(
                    "SELECT sg.student_id, g.name, g.phone, g.telegram_chat_id, sg.relationship FROM student_guardians sg "
                    "JOIN guardians g ON g.id = sg.guardian_id WHERE sg.student_id IN (%s) "
                    "ORDER BY sg.student_id, sg.is_primary DESC, sg.created_at, g.id" % ",".join("?" * len(part)), part):
                out.setdefault(r["student_id"], {"responsavel": r["name"] or "", "celular": r["phone"] or "",
                                                 "parentesco": r["relationship"] or "",
                                                 "telegram": "Vinculado" if r["telegram_chat_id"] else "Não vinculado"})
    return out


def _entry(r: Dict[str, Any]) -> str:
    return r.get("manual_entry") or r.get("cam_entry") or ""


def _late_minutes(r: Dict[str, Any]) -> int:
    if r["status"] != attendance.ATRASADO or not _entry(r) or not r.get("deadline"):
        return 0
    return max(0, _minutes(_entry(r)) - _minutes(r["deadline"]))


def _br(iso: str) -> str:
    return "/".join(reversed(str(iso)[:10].split("-"))) if iso else ""


def _weekday(iso: str) -> str:
    return school.WEEKDAYS[date.fromisoformat(iso).weekday()]


# ================================================================== modelos
def _per_student(rows, contacts) -> List[Dict[str, Any]]:
    per: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        p = per.setdefault(r["student_id"], {
            "id": r["student_id"], "rm": r["registration_number"] or "", "aluno": r["name"] or "(removido)",
            "turma": r["class_name"] or NO_CLASS, "turno": r.get("shift_name") or "", "dias_letivos": 0,
            "esperados": 0, "presencas": 0, "atrasos": 0, "faltas": 0, "justificadas": 0, "saidas": 0,
            "_min": 0, "_run": 0, "maior_sequencia": 0, "ultima_presenca": ""})
        p["dias_letivos"] += 1
        st = r["status"]
        if st != attendance.JUSTIFICADA:
            p["esperados"] += 1
        if st in attendance.PRESENT_SET:
            p["presencas"] += 1
            p["ultima_presenca"] = r["day"]
            p["_run"] = 0
        if st == attendance.ATRASADO:
            p["atrasos"] += 1
            p["_min"] += _late_minutes(r)
        if st == attendance.AUSENTE:
            p["faltas"] += 1
            p["_run"] += 1
            p["maior_sequencia"] = max(p["maior_sequencia"], p["_run"])
        if st == attendance.JUSTIFICADA:
            p["justificadas"] += 1
        if r["early_exit"]:
            p["saidas"] += 1
    out = []
    for p in per.values():
        p["frequencia"] = _pct(p["presencas"], p["esperados"])
        p["pontualidade"] = _pct(p["presencas"] - p["atrasos"], p["presencas"])
        p["media_atraso"] = round(p["_min"] / p["atrasos"], 1) if p["atrasos"] else None
        p["sequencia_atual"] = p["_run"]
        p["_sit"] = _situation(p["frequencia"])
        p["situacao"] = SITUATIONS[p["_sit"]]
        p["ultima_presenca"] = _br(p["ultima_presenca"])
        p.update(contacts.get(p["id"], {}))
        out.append(p)
    return out


def _frequencia(rows, contacts, situacao: str, ordem: str):
    data = _per_student(rows, contacts)
    keep = {"todos": None, "risco": ("abaixo", "atencao"), "abaixo": ("abaixo",), "atencao": ("atencao",),
            "regular": ("regular",)}.get(situacao or "todos")
    if keep:
        data = [p for p in data if p["_sit"] in keep]
    key = {
        "nome": lambda p: p["aluno"].lower(),
        "frequencia": lambda p: (p["frequencia"] if p["frequencia"] is not None else 999, p["aluno"].lower()),
        "faltas": lambda p: (-p["faltas"], p["aluno"].lower()),
    }.get(ordem or "turma", lambda p: (p["turma"].lower(), p["aluno"].lower()))
    return sorted(data, key=key)


def _diario(rows, contacts, status: Sequence[str]):
    wanted = set(status or [])
    out = []
    for r in rows:
        if wanted and not (r["status"] in wanted or (SAIDA in wanted and r["early_exit"])):
            continue
        c = contacts.get(r["student_id"], {})
        out.append({
            "data": _br(r["day"]), "dia_semana": _weekday(r["day"]), "rm": r["registration_number"] or "",
            "aluno": r["name"] or "(removido)", "turma": r["class_name"] or NO_CLASS, "turno": r.get("shift_name") or "",
            "situacao": attendance.LABELS.get(r["status"], r["status"]), "limite": r.get("deadline") or "",
            "entrada": _entry(r), "saida": r.get("cam_exit") or "", "min_atraso": _late_minutes(r) or None,
            "saida_antecipada": "Sim" if r["early_exit"] else "",
            "ajuste": f"{r['manual_by_name']} em {_br(r['manual_at'])} {str(r['manual_at'])[11:16]}"
            if r.get("manual_at") else "",
            "justificativa": (r.get("manual_reason") or "") if r["status"] == attendance.JUSTIFICADA else "",
            "responsavel": c.get("responsavel", ""), "celular": c.get("celular", ""),
        })
    return out


def _turmas(rows):
    per_student = _per_student(rows, {})
    sit: Dict[str, Dict[str, int]] = {}
    for p in per_student:
        t = sit.setdefault(p["turma"], {"abaixo": 0, "atencao": 0})
        if p["_sit"] in t:
            t[p["_sit"]] += 1
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        name = r["class_name"] or NO_CLASS
        g = groups.setdefault(name, {"turma": name, "turno": r.get("shift_name") or "", "_ids": set(), "_days": set(),
                                     "esperados": 0, "presencas": 0, "atrasos": 0, "faltas": 0, "justificadas": 0,
                                     "saidas": 0, "_min": 0})
        g["_ids"].add(r["student_id"])
        g["_days"].add(r["day"])
        st = r["status"]
        g["esperados"] += st != attendance.JUSTIFICADA
        g["presencas"] += st in attendance.PRESENT_SET
        g["atrasos"] += st == attendance.ATRASADO
        g["faltas"] += st == attendance.AUSENTE
        g["justificadas"] += st == attendance.JUSTIFICADA
        g["saidas"] += bool(r["early_exit"])
        g["_min"] += _late_minutes(r)
    out = []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        out.append(dict(g, alunos=len(g["_ids"]), dias_letivos=len(g["_days"]),
                        frequencia=_pct(g["presencas"], g["esperados"]),
                        pontualidade=_pct(g["presencas"] - g["atrasos"], g["presencas"]),
                        media_atraso=round(g["_min"] / g["atrasos"], 1) if g["atrasos"] else None,
                        abaixo=sit.get(name, {}).get("abaixo", 0), atencao=sit.get(name, {}).get("atencao", 0)))
    return out


def _dias(rows):
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(r["day"], {"alunos": 0, "esperados": 0, "presencas": 0, "atrasos": 0, "faltas": 0,
                                         "justificadas": 0, "saidas": 0})
        st = r["status"]
        g["alunos"] += 1
        g["esperados"] += st != attendance.JUSTIFICADA
        g["presencas"] += st in attendance.PRESENT_SET
        g["atrasos"] += st == attendance.ATRASADO
        g["faltas"] += st == attendance.AUSENTE
        g["justificadas"] += st == attendance.JUSTIFICADA
        g["saidas"] += bool(r["early_exit"])
    return [dict(g, data=_br(d), dia_semana=_weekday(d), frequencia=_pct(g["presencas"], g["esperados"]))
            for d, g in sorted(groups.items())]


def build(model: str, columns: Sequence[str], with_contacts: bool, period: str = "week", start: str = "",
          end: str = "", shift_id: Optional[int] = None, class_name: str = "", student_id: Optional[int] = None,
          situacao: str = "todos", status: Sequence[str] = (), ordem: str = "turma",
          with_sample: bool = True) -> Tuple[List[Tuple[str, str, str]], List[List[Any]], date, date]:
    """Devolve (colunas [(chave, título, tipo)], linhas, início, fim)."""
    if model not in MODELS:
        raise ExportError("Escolha o que o arquivo deve ter.")
    allowed = [c for c in MODELS[model]["colunas"] if with_contacts or not c[3]]
    wanted = [c for c in columns if c] if columns else [c[0] for c in allowed if c[2]]
    cols = [(c[0], c[1], c[4]) for c in allowed if c[0] in wanted]
    if not cols:
        raise ExportError("Marque ao menos uma coluna.")
    s, e = resolve_range(period, start, end)
    attendance.ensure_range(s, e)
    rows = _rows(s, e, shift_id, class_name, student_id, with_sample)
    need_contacts = with_contacts and any(c[0] in ("responsavel", "celular", "parentesco", "telegram") for c in cols)
    contacts = _contacts({r["student_id"] for r in rows}) if need_contacts else {}
    if model == "frequencia":
        data = _frequencia(rows, contacts, situacao, ordem)
    elif model == "diario":
        data = _diario(rows, contacts, status)
    elif model == "turmas":
        data = _turmas(rows)
    else:
        data = _dias(rows)
    return cols, [[d.get(c[0]) for c in cols] for d in data], s, e
