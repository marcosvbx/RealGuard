"""
Planilha da base: exportar a base da escola em .xlsx e importar de volta para atualizar em massa.

Abas (nesta ordem, que também é a ordem em que a importação aplica):
  Leia-me · Turnos · Turmas · Alunos · Responsáveis (só para quem vê contatos) · Calendário

Regras da importação (combinadas com a escola):
  * só ADICIONA e ATUALIZA: o que não está na planilha fica como está (nada é apagado);
  * coluna ID (cinza) liga a linha ao cadastro: com ID, a linha atualiza aquele cadastro (e pode
    renomear); sem ID, procura pelo nome (turno, turma), pelo RM (aluno) ou pelo celular/nome
    (responsável) e, se não achar, cria;
  * colunas cinzas são só para consulta e são ignoradas;
  * coluna que não existir na planilha mantém o valor atual (dá para apagar colunas que não interessam);
  * antes de gravar, a tela mostra a prévia linha a linha; linhas com erro são puladas e o resto é
    gravado de uma vez (tudo ou nada no banco).

Fotos do rosto, consentimento e Telegram continuam só pelo painel (não vêm da planilha).
"""

import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.school import xlsx
from app.school.rm import RMError, normalize_rm
from database import guardians as gdb
from database import school
from database.connection import get_db_connection

SHEET_SHIFTS, SHEET_CLASSES, SHEET_STUDENTS = "Turnos", "Turmas", "Alunos"
SHEET_GUARDIANS, SHEET_CALENDAR, SHEET_README = "Responsáveis", "Calendário", "Leia-me"
YES, NO = "Sim", "Não"
NAVY, BLUE, GREEN, ORANGE, GOLD, GREY = "0B2447", "3987E5", "199E70", "D95926", "D6B25E", "7F8CA3"

NEW, CHANGED, SAME, ERROR, SKIPPED = "novo", "alterado", "igual", "erro", "ignorado"


class PlanilhaError(ValueError):
    pass


# ================================================================== util
def _norm(text: Any) -> str:
    """'Início (HH:MM)' -> 'inicio'. Sem acento, minúsculas, sem o que está entre parênteses."""
    text = re.sub(r"\(.*?\)", " ", str(text or ""))
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _yes_no(value: bool) -> str:
    return YES if value else NO


def parse_bool(value: str, default: bool) -> bool:
    v = _norm(value)
    if not v:
        return default
    if v in ("sim", "s", "yes", "y", "1", "true", "verdadeiro", "x", "ativo", "ativa"):
        return True
    if v in ("nao", "n", "no", "0", "false", "falso", "inativo", "inativa"):
        return False
    raise PlanilhaError(f"use {YES} ou {NO} (veio \"{value}\")")


def parse_int(value: str, label: str) -> int:
    v = str(value or "").strip().replace(",", ".")
    try:
        f = float(v)
    except ValueError:
        raise PlanilhaError(f"{label}: use um número inteiro (veio \"{value}\")")
    if not f.is_integer():
        raise PlanilhaError(f"{label}: use um número inteiro (veio \"{value}\")")
    return int(f)


def parse_time(value: str, label: str) -> str:
    """'07:00', '7:00', '7h', '07:00:00' ou o número que o Excel grava para horário (0,2916…)."""
    v = str(value or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})[:h](\d{2})(?::\d{2})?", v) or re.fullmatch(r"(\d{1,2})h", v)
    if m:
        h, mi = int(m.group(1)), int(m.group(2)) if m.lastindex and m.lastindex > 1 else 0
    else:
        try:
            f = float(v.replace(",", "."))
        except ValueError:
            raise PlanilhaError(f"{label}: use HH:MM, ex.: 07:00 (veio \"{value}\")")
        if not 0 <= f < 1:
            raise PlanilhaError(f"{label}: use HH:MM, ex.: 07:00 (veio \"{value}\")")
        total = int(round(f * 24 * 60))
        h, mi = divmod(total, 60)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise PlanilhaError(f"{label}: horário inválido (veio \"{value}\")")
    return f"{h:02d}:{mi:02d}"


def parse_date(value: str, label: str) -> str:
    """AAAA-MM-DD, DD/MM/AAAA, DD/MM/AA ou o número de série de data do Excel."""
    v = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", v):
            return datetime.strptime(v[:10], "%Y-%m-%d").date().isoformat()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", v)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = y + 2000 if y < 100 else y
            return date(y, mo, d).isoformat()
        f = float(v.replace(",", "."))
        if 20000 <= f <= 80000:
            return (date(1899, 12, 30) + timedelta(days=int(f))).isoformat()
    except (ValueError, OverflowError):
        pass
    raise PlanilhaError(f"{label}: use DD/MM/AAAA, ex.: 24/10/2026 (veio \"{value}\")")


def _br(iso: str) -> str:
    return "/".join(reversed(iso.split("-"))) if iso else ""


# ================================================================== exportação
def _load():
    with get_db_connection() as conn:
        students = [dict(r) for r in conn.execute(
            "SELECT s.id, s.registration_number, s.name, s.class_name, s.consent_at, s.ficticio, "
            "(SELECT COUNT(*) FROM student_faces f WHERE f.student_id = s.id) AS faces "
            "FROM students s ORDER BY s.class_name COLLATE NOCASE, s.name COLLATE NOCASE")]
        links = [dict(r) for r in conn.execute(
            "SELECT sg.student_id, sg.guardian_id, sg.relationship, sg.is_primary, g.name, g.phone, "
            "g.telegram_chat_id FROM student_guardians sg JOIN guardians g ON g.id = sg.guardian_id "
            "ORDER BY sg.student_id, sg.is_primary DESC, sg.created_at, g.id")]
    return school.list_shifts(), school.list_classes(), students, links, school.list_calendar()


def _readme(include_guardians: bool, school_name: str) -> List[str]:
    tabs = "Turnos, Turmas, Alunos" + (", Responsáveis" if include_guardians else "") + " e Calendário"
    return [
        f"RealGuard · Planilha da base · {school_name} · {datetime.now():%d/%m/%Y %H:%M}",
        "",
        "## Como usar",
        f"1. Edite as abas {tabs}: corrija o que precisar e acrescente linhas novas no fim de cada aba.",
        "2. Salve como Excel (.xlsx), sem mudar o nome das abas nem os títulos das colunas.",
        "3. No RealGuard: Escola → Planilha da base → Importar. Antes de gravar, a tela mostra a prévia de cada linha.",
        "",
        "## Regras",
        "• A importação só ADICIONA e ATUALIZA. O que não estiver na planilha continua como está: nada é apagado.",
        "• Colunas cinzas são só para consulta: o RealGuard ignora o que for escrito nelas.",
        "• A coluna ID liga a linha ao cadastro. Com ID, a linha atualiza aquele cadastro (dá para renomear "
        "turno e turma). Linha nova: deixe o ID vazio.",
        "• Pode apagar colunas que não vai usar: elas ficam como estão no sistema.",
        "• Linhas com erro aparecem na prévia e são puladas; o resto é gravado.",
        "• Sim/Não: aceita Sim, Não, S, N, 1 ou 0. Horários: HH:MM (07:00). Datas: DD/MM/AAAA.",
        "",
        "## Abas",
        "• Turnos: horário de início, tolerância (minutos de atraso aceitos) e fim das aulas.",
        "• Turmas: cada turma pertence a um turno. Padrão FIAP: ano + EC + turno (A manhã, T tarde, R noite). "
        "Ex.: 2ECR. Renomear a turma leva os alunos junto.",
        "• Alunos: o RM identifica o aluno (RM + 6 números; pode digitar só os números). Turma: uma da aba Turmas. "
        "Fotos do rosto e consentimento são feitos no painel (Alunos → Adicionar foto).",
    ] + ([
        "• Responsáveis: uma linha por responsável de cada aluno (RM do aluno). Irmãos podem ter o mesmo "
        "responsável: use o mesmo celular. Principal = quem aparece primeiro e recebe os avisos. "
        "O Telegram é ligado pelo convite no painel.",
    ] if include_guardians else []) + [
        "• Calendário: feriados, recessos, dias sem aula e dias letivos extras (ex.: sábado letivo). Turno vazio = "
        "vale para a escola toda. Feriados nacionais (origem nacional) não mudam pela planilha.",
        "",
        "## Privacidade (LGPD)",
        "Esta planilha tem dados pessoais de estudantes" + (" e contatos dos responsáveis" if include_guardians else "")
        + ". Guarde com cuidado, não envie por canais abertos e apague as cópias que não forem mais usadas.",
    ]


def export_base(include_guardians: bool = True) -> bytes:
    shifts, classes, students, links, calendar = _load()
    shift_names = [s["name"] for s in shifts]
    class_shift = {c["name"].lower(): c.get("shift_name") or "" for c in classes}
    primary = {}
    for l in links:
        primary.setdefault(l["student_id"], l["name"] or l["phone"])
    by_student = {s["id"]: s for s in students}
    W = xlsx.Column
    sheets = [xlsx.Sheet(SHEET_README, [], [], NAVY, notes=_readme(include_guardians,
                                                                    school.get_settings().get("school_name", "")))]
    sheets.append(xlsx.Sheet(SHEET_SHIFTS, [
        W("ID", 7, "int", readonly=True), W("Turno", 18), W("Início (HH:MM)", 15), W("Tolerância (min)", 16, "int"),
        W("Fim (HH:MM)", 13), W("Ativo", 9, choices=[YES, NO]),
        W("Limite de entrada", 17, readonly=True), W("Turmas", 9, "int", readonly=True),
    ], [[s["id"], s["name"], s["start_time"], s["tolerance_min"], s["end_time"], _yes_no(s["active"]),
         s["deadline"], s["classes"]] for s in shifts], BLUE))
    sheets.append(xlsx.Sheet(SHEET_CLASSES, [
        W("ID", 7, "int", readonly=True), W("Turma", 14),
        W("Turno", 18, choices_ref=xlsx.sheet_ref(SHEET_SHIFTS, 1)), W("Ativa", 9, choices=[YES, NO]),
        W("Alunos", 9, "int", readonly=True),
    ], [[c["id"], c["name"], c.get("shift_name") or "", _yes_no(c["active"]), c["students"]] for c in classes], BLUE))
    sheets.append(xlsx.Sheet(SHEET_STUDENTS, [
        W("ID", 7, "int", readonly=True), W("RM", 12), W("Nome", 34),
        W("Turma", 12, choices_ref=xlsx.sheet_ref(SHEET_CLASSES, 1)), W("Turno", 14, readonly=True),
        W("Fotos do rosto", 14, "int", readonly=True), W("Consentimento", 15, readonly=True),
        W("Responsável principal", 28, readonly=True), W("Fictício", 9, readonly=True),
    ], [[s["id"], s["registration_number"] or "", s["name"], s["class_name"] or "",
         class_shift.get((s["class_name"] or "").lower(), ""), s["faces"],
         _br((s["consent_at"] or "")[:10]) if s["consent_at"] else "",
         primary.get(s["id"], "") if include_guardians else "", YES if s["ficticio"] else ""]
        for s in students], GREEN))
    if include_guardians:
        sheets.append(xlsx.Sheet(SHEET_GUARDIANS, [
            W("ID", 7, "int", readonly=True), W("RM do aluno", 13), W("Aluno", 30, readonly=True),
            W("Nome do responsável", 30), W("Celular", 18),
            W("Parentesco", 18, choices=gdb.RELATIONSHIPS, strict=False), W("Principal", 10, choices=[YES, NO]),
            W("Telegram", 12, readonly=True),
        ], [[l["guardian_id"], (by_student.get(l["student_id"]) or {}).get("registration_number") or "",
             (by_student.get(l["student_id"]) or {}).get("name") or "", l["name"], l["phone"], l["relationship"],
             _yes_no(l["is_primary"]), "Vinculado" if l["telegram_chat_id"] else ""]
            for l in links if l["student_id"] in by_student], ORANGE))
    sheets.append(xlsx.Sheet(SHEET_CALENDAR, [
        W("ID", 7, "int", readonly=True), W("Início", 12), W("Fim", 12),
        W("Tipo", 18, choices=list(school.KINDS.values())), W("Descrição", 36),
        W("Turno", 16, choices_ref=xlsx.sheet_ref(SHEET_SHIFTS, 1)), W("Origem", 10, readonly=True),
    ], [[e["id"], _br(e["start_date"]), _br(e["end_date"]), school.KINDS.get(e["kind"], e["kind"]), e["description"],
         e.get("shift_name") or "", e.get("source") or "escola"] for e in calendar], GOLD))
    return xlsx.write_xlsx(sheets, "RealGuard · Planilha da base")


# ================================================================== leitura da planilha
_SHEET_ALIASES = {
    SHEET_SHIFTS: ("turnos", "turno"), SHEET_CLASSES: ("turmas", "turma"), SHEET_STUDENTS: ("alunos", "aluno"),
    SHEET_GUARDIANS: ("responsaveis", "responsavel"), SHEET_CALENDAR: ("calendario", "calendario escolar"),
}
_COLUMNS = {
    SHEET_SHIFTS: {"id": ("id",), "nome": ("turno", "nome", "nome do turno"), "inicio": ("inicio", "inicio das aulas"),
                   "tol": ("tolerancia", "tolerancia min"), "fim": ("fim", "fim das aulas"), "ativo": ("ativo", "ativa")},
    SHEET_CLASSES: {"id": ("id",), "nome": ("turma", "nome", "nome da turma"), "turno": ("turno",),
                    "ativo": ("ativa", "ativo")},
    SHEET_STUDENTS: {"id": ("id",), "rm": ("rm", "registro", "matricula"), "nome": ("nome", "aluno", "nome do aluno"),
                     "turma": ("turma",)},
    SHEET_GUARDIANS: {"id": ("id",), "rm": ("rm do aluno", "rm"), "nome": ("nome do responsavel", "responsavel", "nome"),
                      "celular": ("celular", "telefone", "whatsapp"), "parentesco": ("parentesco",),
                      "principal": ("principal",)},
    SHEET_CALENDAR: {"id": ("id",), "inicio": ("inicio", "data", "data inicial"), "fim": ("fim", "data final"),
                     "tipo": ("tipo",), "descricao": ("descricao", "motivo"), "turno": ("turno",)},
}
_REQUIRED = {SHEET_SHIFTS: ("nome",), SHEET_CLASSES: ("nome",), SHEET_STUDENTS: ("rm",),
             SHEET_GUARDIANS: ("rm",), SHEET_CALENDAR: ("inicio",)}


class _Rows:
    """Linhas de uma aba com acesso pela coluna (None = coluna não está na planilha)."""

    def __init__(self, sheet: str, rows: List[Dict[str, Any]]):
        self.sheet, self.index, self.rows, self.missing = sheet, {}, [], []
        if not rows:
            return
        header = [_norm(h) for h in rows[0]["celulas"]]
        for key, aliases in _COLUMNS[sheet].items():
            pos = next((i for i, h in enumerate(header) if h in aliases), None)
            if pos is not None:
                self.index[key] = pos
        self.missing = [k for k in _REQUIRED[sheet] if k not in self.index]
        self.rows = rows[1:]

    def get(self, row: Dict[str, Any], key: str) -> Optional[str]:
        pos = self.index.get(key)
        if pos is None:
            return None
        cells = row["celulas"]
        return str(cells[pos]).strip() if pos < len(cells) else ""

    def blank(self, row: Dict[str, Any]) -> bool:
        return all(not self.get(row, k) for k in self.index)


def read_workbook(content: bytes) -> Dict[str, _Rows]:
    try:
        book = xlsx.read_xlsx(content)
    except xlsx.XlsxError as e:
        raise PlanilhaError(str(e))
    out: Dict[str, _Rows] = {}
    for name, rows in book.items():
        n = _norm(name)
        for sheet, aliases in _SHEET_ALIASES.items():
            if n in aliases and sheet not in out:
                out[sheet] = _Rows(sheet, rows)
    if not out:
        raise PlanilhaError("Nenhuma aba reconhecida. Use a planilha exportada pelo RealGuard "
                            "(abas Turnos, Turmas, Alunos, Responsáveis e Calendário).")
    return out


# ================================================================== plano (prévia)
class Plan:
    def __init__(self):
        self.items: List[Dict[str, Any]] = []
        self.ops: List[Tuple[str, Dict[str, Any]]] = []
        self.sheets: List[str] = []

    def add(self, sheet: str, line: int, action: str, key: str, detail: str = "") -> None:
        self.items.append({"aba": sheet, "linha": line, "acao": action, "chave": key, "detalhe": detail})

    def summary(self) -> Dict[str, Any]:
        tabs: Dict[str, Dict[str, int]] = {}
        for sheet in self.sheets:
            tabs[sheet] = {NEW: 0, CHANGED: 0, SAME: 0, ERROR: 0, SKIPPED: 0}
        for it in self.items:
            tabs.setdefault(it["aba"], {NEW: 0, CHANGED: 0, SAME: 0, ERROR: 0, SKIPPED: 0})[it["acao"]] += 1
        total = {k: sum(t[k] for t in tabs.values()) for k in (NEW, CHANGED, SAME, ERROR, SKIPPED)}
        return {"abas": tabs, "total": total, "gravar": total[NEW] + total[CHANGED]}

    def as_dict(self) -> Dict[str, Any]:
        order = {ERROR: 0, SKIPPED: 1, NEW: 2, CHANGED: 3, SAME: 4}
        items = sorted(self.items, key=lambda x: (self.sheets.index(x["aba"]) if x["aba"] in self.sheets else 99,
                                                   order[x["acao"]], x["linha"]))
        return {"resumo": self.summary(), "itens": items, "abas_lidas": self.sheets}


def _diff(pairs) -> str:
    return " · ".join(f"{label}: {old or '—'} → {new or '—'}" for label, old, new in pairs if (old or "") != (new or ""))


def build_plan(content: bytes, include_guardians: bool = True) -> Plan:
    book = read_workbook(content)
    plan = Plan()
    shifts, classes, students, links, calendar = _load()
    settings = school.get_settings()
    default_shift = str(settings.get("default_shift_id") or "")

    # -------------------------------------------------------------- turnos
    # referência final de turno: nome em minúsculas -> ("id", n) | ("new", nome)
    shift_ref: Dict[str, Tuple[str, Any]] = {s["name"].lower(): ("id", s["id"]) for s in shifts}
    shift_by_id = {s["id"]: s for s in shifts}
    sh = book.get(SHEET_SHIFTS)
    if sh is not None:
        plan.sheets.append(SHEET_SHIFTS)
        if sh.missing:
            plan.add(SHEET_SHIFTS, 1, ERROR, "cabeçalho", "Faltou a coluna Turno.")
        else:
            records = []
            for row in sh.rows:
                if sh.blank(row):
                    continue
                line, rid = row["linha"], sh.get(row, "id")
                name = sh.get(row, "nome") or ""
                try:
                    cur = None
                    if rid:
                        cur = shift_by_id.get(parse_int(rid, "ID"))
                        if not cur:
                            raise PlanilhaError(f"ID {rid} não existe. Para criar um turno novo, deixe o ID vazio.")
                    elif name.lower() in shift_ref:
                        cur = shift_by_id[shift_ref[name.lower()][1]]
                    start = sh.get(row, "inicio")
                    end = sh.get(row, "fim")
                    tol = sh.get(row, "tol")
                    active = sh.get(row, "ativo")
                    rec = {
                        "name": name or (cur["name"] if cur else ""),
                        "start": parse_time(start, "Início") if start else (cur["start_time"] if cur else ""),
                        "end": parse_time(end, "Fim") if end else (cur["end_time"] if cur else ""),
                        "tol": parse_int(tol, "Tolerância") if tol else (cur["tolerance_min"] if cur else 15),
                        "active": parse_bool(active, cur["active"] if cur else True) if active is not None
                        else (cur["active"] if cur else True),
                    }
                    if not cur and not (start and end):
                        raise PlanilhaError("Turno novo: informe Início e Fim.")
                    rec["name"], rec["start"], rec["tol"], rec["end"] = school._check_shift(
                        rec["name"], rec["start"], rec["tol"], rec["end"])
                    if cur and not rec["active"] and str(cur["id"]) == default_shift:
                        raise PlanilhaError("é o turno padrão (de quem não tem turma): não pode ser desativado.")
                    records.append((line, cur, rec))
                except (PlanilhaError, school.SchoolError) as e:
                    plan.add(SHEET_SHIFTS, line, ERROR, name or f"ID {rid}", str(e))
            final = {s["id"]: s["name"].lower() for s in shifts}
            for _, cur, rec in records:
                if cur:
                    final[cur["id"]] = rec["name"].lower()
            seen = set()
            for line, cur, rec in records:
                key = rec["name"].lower()
                clash = any(n == key and sid != (cur or {}).get("id") for sid, n in final.items())
                if key in seen or clash:
                    plan.add(SHEET_SHIFTS, line, ERROR, rec["name"], "Nome repetido: já existe outro turno com esse nome.")
                    continue
                seen.add(key)
                if cur:
                    detail = _diff([("Nome", cur["name"], rec["name"]), ("Início", cur["start_time"], rec["start"]),
                                    ("Tolerância", str(cur["tolerance_min"]), str(rec["tol"])),
                                    ("Fim", cur["end_time"], rec["end"]),
                                    ("Ativo", _yes_no(cur["active"]), _yes_no(rec["active"]))])
                    plan.add(SHEET_SHIFTS, line, CHANGED if detail else SAME, rec["name"], detail)
                    if detail:
                        plan.ops.append(("shift_update", dict(rec, id=cur["id"])))
                        shift_ref.pop(cur["name"].lower(), None)
                        shift_ref[key] = ("id", cur["id"])
                else:
                    plan.add(SHEET_SHIFTS, line, NEW, rec["name"],
                             f"{rec['start']} às {rec['end']}, tolerância {rec['tol']} min")
                    plan.ops.append(("shift_insert", rec))
                    shift_ref[key] = ("new", rec["name"])
            for _, cur, rec in records:     # nome antigo de turno renomeado ainda vale nas outras abas
                if cur and cur["name"].lower() != rec["name"].lower():
                    shift_ref.setdefault(cur["name"].lower(), ("id", cur["id"]))

    def shift_of(text: str) -> Optional[Tuple[str, Any]]:
        return shift_ref.get((text or "").strip().lower())

    # -------------------------------------------------------------- turmas
    class_ref: Dict[str, str] = {c["name"].lower(): c["name"] for c in classes}   # minúsculas -> nome final
    class_by_id = {c["id"]: c for c in classes}
    ct = book.get(SHEET_CLASSES)
    if ct is not None:
        plan.sheets.append(SHEET_CLASSES)
        if ct.missing:
            plan.add(SHEET_CLASSES, 1, ERROR, "cabeçalho", "Faltou a coluna Turma.")
        else:
            records = []
            for row in ct.rows:
                if ct.blank(row):
                    continue
                line, rid, name = row["linha"], ct.get(row, "id"), (ct.get(row, "nome") or "").strip()
                try:
                    cur = None
                    if rid:
                        cur = class_by_id.get(parse_int(rid, "ID"))
                        if not cur:
                            raise PlanilhaError(f"ID {rid} não existe. Para criar uma turma nova, deixe o ID vazio.")
                    else:
                        cur = next((c for c in classes if c["name"].lower() == name.lower()), None)
                    name = name or (cur["name"] if cur else "")
                    if not 1 <= len(name) <= 40:
                        raise PlanilhaError("Nome da turma: de 1 a 40 caracteres.")
                    turno = ct.get(row, "turno")
                    if turno:
                        ref = shift_of(turno)
                        if not ref:
                            raise PlanilhaError(f"Turno \"{turno}\" não existe (cadastre na aba Turnos).")
                    elif cur:
                        ref = ("id", cur["shift_id"])
                    else:
                        raise PlanilhaError("Turma nova: informe o turno.")
                    active = ct.get(row, "ativo")
                    rec = {"name": name, "shift": ref,
                           "active": parse_bool(active, cur["active"] if cur else True) if active is not None
                           else (cur["active"] if cur else True)}
                    records.append((line, cur, rec))
                except PlanilhaError as e:
                    plan.add(SHEET_CLASSES, line, ERROR, name or f"ID {rid}", str(e))
            final = {c["id"]: c["name"].lower() for c in classes}
            for _, cur, rec in records:
                if cur:
                    final[cur["id"]] = rec["name"].lower()
            seen = set()
            shift_name_by_id = {s["id"]: s["name"] for s in shifts}
            for line, cur, rec in records:
                key = rec["name"].lower()
                if key in seen or any(n == key and cid != (cur or {}).get("id") for cid, n in final.items()):
                    plan.add(SHEET_CLASSES, line, ERROR, rec["name"], "Nome repetido: já existe outra turma com esse nome.")
                    continue
                seen.add(key)
                new_shift = rec["shift"][1] if rec["shift"][0] == "new" else shift_name_by_id.get(rec["shift"][1], "")
                if rec["shift"][0] == "id":
                    # nome final do turno (pode ter sido renomeado nesta planilha)
                    new_shift = next((op[1]["name"] for op in plan.ops
                                      if op[0] == "shift_update" and op[1]["id"] == rec["shift"][1]), new_shift)
                if cur:
                    same_shift = rec["shift"] == ("id", cur["shift_id"])
                    detail = _diff([("Nome", cur["name"], rec["name"]),
                                    ("Turno", cur.get("shift_name") or "", (cur.get("shift_name") or "")
                                     if same_shift else new_shift),
                                    ("Ativa", _yes_no(cur["active"]), _yes_no(rec["active"]))])
                    if cur["name"] != rec["name"] and cur["students"]:
                        detail += f" (leva {cur['students']} aluno(s) junto)"
                    plan.add(SHEET_CLASSES, line, CHANGED if detail else SAME, rec["name"], detail)
                    if detail:
                        plan.ops.append(("class_update", dict(rec, id=cur["id"], old_name=cur["name"])))
                        class_ref.pop(cur["name"].lower(), None)
                        class_ref[key] = rec["name"]
                else:
                    plan.add(SHEET_CLASSES, line, NEW, rec["name"], f"turno {new_shift}")
                    plan.ops.append(("class_insert", rec))
                    class_ref[key] = rec["name"]
            for _, cur, rec in records:     # nome antigo de turma renomeada ainda vale na aba Alunos
                if cur and cur["name"].lower() != rec["name"].lower():
                    class_ref.setdefault(cur["name"].lower(), rec["name"])

    # -------------------------------------------------------------- alunos
    stu_by_id = {s["id"]: s for s in students}
    stu_by_rm = {(s["registration_number"] or "").upper(): s for s in students if s["registration_number"]}
    student_ref: Dict[str, Tuple[str, Any]] = {rm: ("id", s["id"]) for rm, s in stu_by_rm.items()}
    renamed_rm: List[Tuple[str, int]] = []

    def _eff_class(cur) -> str:
        """Turma atual do aluno já com o nome novo, se a turma foi renomeada nesta planilha."""
        if not cur:
            return ""
        name = cur["class_name"] or ""
        return class_ref.get(name.lower(), name) if name else ""
    st = book.get(SHEET_STUDENTS)
    if st is not None:
        plan.sheets.append(SHEET_STUDENTS)
        if st.missing:
            plan.add(SHEET_STUDENTS, 1, ERROR, "cabeçalho", "Faltou a coluna RM.")
        else:
            seen_rm, final_rm = set(), {s["id"]: (s["registration_number"] or "").upper() for s in students}
            records = []
            for row in st.rows:
                if st.blank(row):
                    continue
                line, rid = row["linha"], st.get(row, "id")
                raw_rm, name = (st.get(row, "rm") or "").strip(), (st.get(row, "nome") or "").strip()
                try:
                    cur = None
                    if rid:
                        cur = stu_by_id.get(parse_int(rid, "ID"))
                        if not cur:
                            raise PlanilhaError(f"ID {rid} não existe. Para cadastrar um aluno novo, deixe o ID vazio.")
                    if cur and raw_rm.upper() == (cur["registration_number"] or "").upper():
                        rm = cur["registration_number"]          # RM antigo fora do padrão continua valendo
                    else:
                        if not raw_rm:
                            raise PlanilhaError("Informe o RM.")
                        rm = normalize_rm(raw_rm)
                        if not cur:
                            cur = stu_by_rm.get(rm.upper())
                    name = name or (cur["name"] if cur else "")
                    if not name:
                        raise PlanilhaError("Aluno novo: informe o nome.")
                    if len(name) > 120:
                        raise PlanilhaError("Nome longo demais (máximo 120 caracteres).")
                    turma = st.get(row, "turma")
                    if turma is None:
                        cls = _eff_class(cur)
                    elif turma == "":
                        cls = ""
                    else:
                        cls = class_ref.get(turma.lower())
                        if cls is None:
                            raise PlanilhaError(f"Turma \"{turma}\" não existe: cadastre na aba Turmas "
                                               "(se ela foi renomeada, exporte a planilha de novo).")
                    records.append((line, cur, {"rm": rm, "name": name, "class": cls}))
                except (PlanilhaError, RMError) as e:
                    plan.add(SHEET_STUDENTS, line, ERROR, raw_rm or name or f"ID {rid}", str(e))
            for _, cur, rec in records:
                if cur:
                    final_rm[cur["id"]] = rec["rm"].upper()
            for line, cur, rec in records:
                key = rec["rm"].upper()
                if key in seen_rm or any(r == key and sid != (cur or {}).get("id") for sid, r in final_rm.items()):
                    plan.add(SHEET_STUDENTS, line, ERROR, rec["rm"], "RM repetido: já pertence a outro aluno.")
                    continue
                seen_rm.add(key)
                if cur:
                    # turma renomeada na aba Turmas já leva o aluno: não conta como mudança dele
                    detail = _diff([("RM", cur["registration_number"], rec["rm"]), ("Nome", cur["name"], rec["name"]),
                                    ("Turma", _eff_class(cur), rec["class"])])
                    plan.add(SHEET_STUDENTS, line, CHANGED if detail else SAME, f"{rec['rm']} {rec['name']}", detail)
                    if detail:
                        plan.ops.append(("student_update", dict(rec, id=cur["id"])))
                    student_ref.pop((cur["registration_number"] or "").upper(), None)
                    student_ref[key] = ("id", cur["id"])
                    renamed_rm.append(((cur["registration_number"] or "").upper(), cur["id"]))
                else:
                    plan.add(SHEET_STUDENTS, line, NEW, f"{rec['rm']} {rec['name']}",
                             f"turma {rec['class'] or 'sem turma'} · rosto e consentimento depois, no painel")
                    plan.ops.append(("student_insert", rec))
                    student_ref[key] = ("new", rec["rm"])
            for old, sid in renamed_rm:     # RM antigo ainda vale na aba Responsáveis
                if old:
                    student_ref.setdefault(old, ("id", sid))

    # -------------------------------------------------------------- responsáveis
    gt = book.get(SHEET_GUARDIANS) if include_guardians else None
    if SHEET_GUARDIANS in book and not include_guardians:
        plan.sheets.append(SHEET_GUARDIANS)
        plan.add(SHEET_GUARDIANS, 1, SKIPPED, "aba inteira", "Seu perfil não vê os contatos dos responsáveis.")
    if gt is not None:
        plan.sheets.append(SHEET_GUARDIANS)
        if gt.missing:
            plan.add(SHEET_GUARDIANS, 1, ERROR, "cabeçalho", "Faltou a coluna RM do aluno.")
        else:
            g_by_id: Dict[int, Dict[str, Any]] = {}
            links_of: Dict[int, List[Dict[str, Any]]] = {}
            for l in links:
                g_by_id.setdefault(l["guardian_id"], {"id": l["guardian_id"], "name": l["name"], "phone": l["phone"]})
                links_of.setdefault(l["student_id"], []).append(l)
            all_g = list(g_by_id.values())
            new_by_phone: Dict[str, str] = {}      # celular normalizado -> chave do responsável novo (irmãos)
            touched: Dict[Any, Tuple[str, str, int]] = {}   # responsável -> (nome, celular, linha) para conferir
            primary_seen: Dict[Any, int] = {}
            n_new = 0
            for row in gt.rows:
                if gt.blank(row):
                    continue
                line, rid = row["linha"], gt.get(row, "id")
                raw_rm = (gt.get(row, "rm") or "").strip()
                try:
                    if not raw_rm:
                        raise PlanilhaError("Informe o RM do aluno.")
                    try:
                        rmk = normalize_rm(raw_rm).upper()
                    except RMError:
                        rmk = raw_rm.upper()
                    sref = student_ref.get(rmk)
                    if not sref:
                        raise PlanilhaError(f"Aluno {raw_rm} não existe (cadastre na aba Alunos).")
                    name_in, phone_in = gt.get(row, "nome"), gt.get(row, "celular")
                    rel_in, prim_in = gt.get(row, "parentesco"), gt.get(row, "principal")
                    phone = gdb.clean_phone(phone_in) if phone_in else ""
                    existing_links = links_of.get(sref[1], []) if sref[0] == "id" else []
                    cur_g, cur_link = None, None
                    if rid:
                        cur_g = g_by_id.get(parse_int(rid, "ID"))
                        if not cur_g:
                            raise PlanilhaError(f"ID {rid} não existe. Para um responsável novo, deixe o ID vazio.")
                    else:
                        cur_link = next((l for l in existing_links if phone and gdb.phones_match(l["phone"], phone)),
                                        None) or next((l for l in existing_links if name_in and
                                                       (l["name"] or "").strip().lower() == name_in.strip().lower()),
                                                      None)
                        if cur_link:
                            cur_g = g_by_id[cur_link["guardian_id"]]
                        elif phone:
                            cur_g = next((g for g in all_g if gdb.phones_match(g["phone"], phone)), None)
                    if cur_g and not cur_link:
                        cur_link = next((l for l in existing_links if l["guardian_id"] == cur_g["id"]), None)
                    name = (name_in if name_in is not None else (cur_g["name"] if cur_g else "")).strip()[:80]
                    if phone_in is None:
                        phone = cur_g["phone"] if cur_g else ""
                    if not (name or phone):
                        raise PlanilhaError("Informe o nome ou o celular do responsável.")
                    rel = gdb.clean_relationship(rel_in if rel_in is not None else
                                                 (cur_link["relationship"] if cur_link else ""))
                    primary = parse_bool(prim_in, bool(cur_link and cur_link["is_primary"])) if prim_in is not None \
                        else bool(cur_link and cur_link["is_primary"])
                    if primary:
                        if sref in primary_seen:
                            raise PlanilhaError(f"O aluno já tem outro responsável marcado como principal "
                                                f"(linha {primary_seen[sref]}).")
                        primary_seen[sref] = line
                    gkey: Any = ("id", cur_g["id"]) if cur_g else None
                    if not gkey:
                        pn = gdb.normalize_phone(phone)
                        if pn and pn in new_by_phone:
                            gkey = ("new", new_by_phone[pn])
                        else:
                            n_new += 1
                            gkey = ("new", f"g{n_new}")
                            if pn:
                                new_by_phone[pn] = gkey[1]
                    if gkey in touched and (touched[gkey][0], gdb.normalize_phone(touched[gkey][1])) != \
                            (name, gdb.normalize_phone(phone)):
                        raise PlanilhaError(f"Mesmo responsável com nome ou celular diferente da linha "
                                            f"{touched[gkey][2]}. Deixe igual nas duas linhas.")
                    touched.setdefault(gkey, (name, phone, line))
                    label = f"{raw_rm} · {name or phone}"
                    rec = {"guardian": gkey, "student": sref, "name": name, "phone": phone, "relationship": rel,
                           "primary": primary}
                    if cur_g and cur_link:
                        detail = _diff([("Nome", cur_g["name"], name), ("Celular", cur_g["phone"], phone),
                                        ("Parentesco", cur_link["relationship"], rel),
                                        ("Principal", _yes_no(cur_link["is_primary"]), _yes_no(primary))])
                        plan.add(SHEET_GUARDIANS, line, CHANGED if detail else SAME, label, detail)
                        if detail:
                            plan.ops.append(("guardian_upsert", rec))
                    else:
                        how = ("já cadastrado em outro aluno (mesmo celular): só liga" if cur_g
                               else "responsável novo")
                        plan.add(SHEET_GUARDIANS, line, NEW, label, f"{rel} · {how}")
                        plan.ops.append(("guardian_upsert", rec))
                except (PlanilhaError, gdb.GuardianError) as e:
                    plan.add(SHEET_GUARDIANS, line, ERROR, raw_rm or f"ID {rid}", str(e))

    # -------------------------------------------------------------- calendário
    cal = book.get(SHEET_CALENDAR)
    if cal is not None:
        plan.sheets.append(SHEET_CALENDAR)
        if cal.missing:
            plan.add(SHEET_CALENDAR, 1, ERROR, "cabeçalho", "Faltou a coluna Início.")
        else:
            by_id = {e["id"]: e for e in calendar}
            kinds = {_norm(v): k for k, v in school.KINDS.items()}
            kinds.update({_norm(k): k for k in school.KINDS})
            kinds.update({"sabado letivo": "DIA_LETIVO", "dia letivo": "DIA_LETIVO", "sem aulas": "SEM_AULA"})
            seen = set()
            for row in cal.rows:
                if cal.blank(row):
                    continue
                line, rid = row["linha"], cal.get(row, "id")
                desc_in = cal.get(row, "descricao")
                try:
                    cur = None
                    if rid:
                        cur = by_id.get(parse_int(rid, "ID"))
                        if not cur:
                            raise PlanilhaError(f"ID {rid} não existe. Para um item novo, deixe o ID vazio.")
                    s_in, e_in, k_in, t_in = cal.get(row, "inicio"), cal.get(row, "fim"), cal.get(row, "tipo"), \
                        cal.get(row, "turno")
                    start = parse_date(s_in, "Início") if s_in else (cur["start_date"] if cur else "")
                    if not start:
                        raise PlanilhaError("Informe a data de início.")
                    end = parse_date(e_in, "Fim") if e_in else (cur["end_date"] if cur and e_in is None else start)
                    if end < start:
                        raise PlanilhaError("A data final é antes da inicial.")
                    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 366:
                        raise PlanilhaError("Período longo demais (máximo de 1 ano).")
                    if k_in:
                        kind = kinds.get(_norm(k_in))
                        if not kind:
                            raise PlanilhaError(f"Tipo \"{k_in}\": use " + ", ".join(school.KINDS.values()) + ".")
                    elif cur:
                        kind = cur["kind"]
                    else:
                        raise PlanilhaError("Informe o tipo (Feriado, Recesso, Sem aula ou Dia letivo extra).")
                    desc = (desc_in if desc_in is not None else (cur["description"] if cur else "")).strip()[:120]
                    if not desc:
                        raise PlanilhaError("Descreva o motivo (ex.: Feriado municipal).")
                    if t_in:
                        sref = shift_of(t_in)
                        if not sref:
                            raise PlanilhaError(f"Turno \"{t_in}\" não existe (cadastre na aba Turnos).")
                    elif t_in == "" or not cur:
                        sref = None
                    else:
                        sref = ("id", cur["shift_id"]) if cur["shift_id"] else None
                    rec = {"start": start, "end": end, "kind": kind, "description": desc, "shift": sref}
                    key = (start, end, kind, desc.lower(), sref)
                    label = f"{_br(start)}{' a ' + _br(end) if end != start else ''} · {desc}"
                    if key in seen:
                        plan.add(SHEET_CALENDAR, line, ERROR, label, "Linha repetida nesta planilha.")
                        continue
                    seen.add(key)
                    if cur:
                        cur_shift = ("id", cur["shift_id"]) if cur["shift_id"] else None
                        detail = _diff([("Início", _br(cur["start_date"]), _br(start)),
                                        ("Fim", _br(cur["end_date"]), _br(end)),
                                        ("Tipo", school.KINDS.get(cur["kind"]), school.KINDS.get(kind)),
                                        ("Descrição", cur["description"], desc),
                                        ("Turno", cur.get("shift_name") or "todos",
                                         (cur.get("shift_name") or "todos") if sref == cur_shift else (t_in or "todos"))])
                        if detail and (cur.get("source") or "escola") != "escola":
                            plan.add(SHEET_CALENDAR, line, SKIPPED, label,
                                     "Feriado nacional: não muda pela planilha (ajuste em Escola → Calendário).")
                            continue
                        plan.add(SHEET_CALENDAR, line, CHANGED if detail else SAME, label, detail)
                        if detail:
                            plan.ops.append(("calendar_update", dict(rec, id=cur["id"])))
                    else:
                        twin = next((e for e in calendar if (e["start_date"], e["end_date"], e["kind"],
                                                             e["description"].lower()) == (start, end, kind, desc.lower())
                                     and (("id", e["shift_id"]) if e["shift_id"] else None) == sref), None)
                        if twin:
                            plan.add(SHEET_CALENDAR, line, SAME, label, "já está no calendário")
                            continue
                        plan.add(SHEET_CALENDAR, line, NEW, label, school.KINDS[kind] +
                                 (f" · turno {t_in}" if t_in else " · escola toda"))
                        plan.ops.append(("calendar_insert", rec))
                except (PlanilhaError, ValueError) as e:
                    plan.add(SHEET_CALENDAR, line, ERROR, desc_in or s_in or f"ID {rid}", str(e))
    return plan


# ================================================================== gravação
def apply_plan(plan: Plan) -> Dict[str, int]:
    """Grava tudo numa transação só: ou entra tudo (menos as linhas com erro), ou nada."""
    counts = {NEW: 0, CHANGED: 0}
    new_shift: Dict[str, int] = {}
    new_student: Dict[str, int] = {}
    new_guardian: Dict[str, int] = {}
    touched_students = set()

    def shift_id(ref) -> Optional[int]:
        if ref is None:
            return None
        return new_shift[ref[1]] if ref[0] == "new" else int(ref[1])

    with get_db_connection() as conn:
        try:
            for op, d in plan.ops:
                if op == "shift_insert":
                    cur = conn.execute("INSERT INTO shifts (name, start_time, tolerance_min, end_time, active) "
                                       "VALUES (?, ?, ?, ?, ?)", (d["name"], d["start"], d["tol"], d["end"],
                                                                 int(d["active"])))
                    new_shift[d["name"]] = cur.lastrowid
                elif op == "shift_update":
                    conn.execute("UPDATE shifts SET name = ?, start_time = ?, tolerance_min = ?, end_time = ?, "
                                 "active = ? WHERE id = ?", (d["name"], d["start"], d["tol"], d["end"],
                                                             int(d["active"]), d["id"]))
                elif op == "class_insert":
                    conn.execute("INSERT INTO classes (name, shift_id, active) VALUES (?, ?, ?)",
                                 (d["name"], shift_id(d["shift"]), int(d["active"])))
                elif op == "class_update":
                    conn.execute("UPDATE classes SET name = ?, shift_id = ?, active = ? WHERE id = ?",
                                 (d["name"], shift_id(d["shift"]), int(d["active"]), d["id"]))
                    if d["name"] != d["old_name"]:     # renomear leva os alunos junto
                        conn.execute("UPDATE students SET class_name = ? WHERE class_name = ? COLLATE NOCASE",
                                     (d["name"], d["old_name"]))
                elif op == "student_insert":
                    cur = conn.execute("INSERT INTO students (registration_number, name, class_name) VALUES (?, ?, ?)",
                                       (d["rm"], d["name"], d["class"]))
                    new_student[d["rm"].upper()] = cur.lastrowid
                elif op == "student_update":
                    conn.execute("UPDATE students SET registration_number = ?, name = ?, class_name = ? WHERE id = ?",
                                 (d["rm"], d["name"], d["class"], d["id"]))
                elif op == "guardian_upsert":
                    sid = new_student[d["student"][1].upper()] if d["student"][0] == "new" else int(d["student"][1])
                    g = d["guardian"]
                    if g[0] == "new" and g[1] not in new_guardian:
                        new_guardian[g[1]] = conn.execute("INSERT INTO guardians (name, phone) VALUES (?, ?)",
                                                          (d["name"], d["phone"])).lastrowid
                    gid = new_guardian[g[1]] if g[0] == "new" else int(g[1])
                    conn.execute("UPDATE guardians SET name = ?, phone = ? WHERE id = ?", (d["name"], d["phone"], gid))
                    if d["primary"]:
                        conn.execute("UPDATE student_guardians SET is_primary = 0 WHERE student_id = ?", (sid,))
                    conn.execute("INSERT INTO student_guardians (student_id, guardian_id, relationship, is_primary) "
                                 "VALUES (?, ?, ?, ?) ON CONFLICT(student_id, guardian_id) DO UPDATE SET "
                                 "relationship = excluded.relationship, is_primary = excluded.is_primary",
                                 (sid, gid, d["relationship"], int(d["primary"])))
                    touched_students.add(sid)
                    for (other,) in conn.execute("SELECT student_id FROM student_guardians WHERE guardian_id = ?",
                                                 (gid,)).fetchall():
                        touched_students.add(other)
                elif op == "calendar_insert":
                    conn.execute("INSERT INTO calendar_entries (start_date, end_date, kind, description, shift_id, "
                                 "source) VALUES (?, ?, ?, ?, ?, 'escola')",
                                 (d["start"], d["end"], d["kind"], d["description"], shift_id(d["shift"])))
                elif op == "calendar_update":
                    conn.execute("UPDATE calendar_entries SET start_date = ?, end_date = ?, kind = ?, description = ?, "
                                 "shift_id = ? WHERE id = ?", (d["start"], d["end"], d["kind"], d["description"],
                                                                shift_id(d["shift"]), d["id"]))
            for sid in touched_students:
                gdb._ensure_primary(conn, sid)
                gdb.sync_student(conn, sid)
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise PlanilhaError(f"Nada foi gravado: o banco recusou a importação ({e}).")
    for it in plan.items:
        if it["acao"] in counts:
            counts[it["acao"]] += 1
    return counts
