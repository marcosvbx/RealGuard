"""
Persistência da estrutura escolar: turnos, turmas, calendário e regras.

Tudo aqui é editável pela tela Escola; os valores semeados na migração são
EXEMPLOS (turnos Manhã/Tarde/Noite, feriados nacionais, um recesso de julho).
A turma do aluno continua sendo `students.class_name`, ligada a `classes.name`
(renomear a turma atualiza os alunos) — assim nada do que já existe quebra.
"""

import logging
import re
import sqlite3
from datetime import timedelta
from config.clock import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.school.holidays import national_holidays
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_FMT = "%Y-%m-%d"
KINDS = {
    "FERIADO": "Feriado",
    "RECESSO": "Recesso",
    "SEM_AULA": "Sem aula",
    "DIA_LETIVO": "Dia letivo extra",
}
NO_SCHOOL_KINDS = ("FERIADO", "RECESSO", "SEM_AULA")
WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

DEFAULT_CONSENT_TEXT = """TERMO DE CONSENTIMENTO PARA USO DE IMAGEM FACIAL (BIOMETRIA)

Eu, {responsavel}, responsável legal por {aluno} (RM {rm}, turma {turma}), autorizo a {escola} a utilizar a imagem do rosto do(a) estudante exclusivamente para o registro automático de entrada e saída pelo sistema RealGuard.

Fico ciente de que:
1. Não é guardada a foto: o sistema armazena apenas um vetor numérico (biometria) gerado a partir dela, no equipamento da escola, sem envio para a internet.
2. A finalidade é exclusivamente o controle de frequência e a segurança na entrada e saída, com aviso ao responsável.
3. Posso revogar este consentimento a qualquer momento junto à secretaria; nesse caso, a biometria é apagada e a frequência passa a ser registrada manualmente.
4. Os dados são tratados conforme a Lei Geral de Proteção de Dados (Lei 13.709/2018), em especial o art. 14 (dados de crianças e adolescentes).

Local e data: ____________________, ____/____/______

Assinatura do responsável: ______________________________________

(Modelo editável. Recomenda-se revisão jurídica antes do uso.)"""

DEFAULT_SETTINGS = {
    "school_name": "Escola Exemplo",
    "school_days": "1,2,3,4,5",          # dias da semana com aula (1 = segunda ... 7 = domingo)
    "year_start": "",                     # preenchido na semeadura: 02/02 do ano corrente
    "year_end": "",                       # 18/12 do ano corrente
    "default_shift_id": "",               # turno de quem não tem turma
    "auto_exit_min_minutes": "30",        # câmera "Automática": intervalo mínimo entre passagens
    "absence_notice_after_minutes": "60",  # aviso automático de ausência: minutos depois da tolerância (0 = manual)
    "retention_photo_days": "30",         # fotos de alertas
    "retention_passages_days": "180",     # passagens brutas da câmera
    "consent_text": DEFAULT_CONSENT_TEXT,
    "consent_version": "1",
    "holiday_years": "",
}


class SchoolError(ValueError):
    """Erro de regra (mensagem pronta para o usuário)."""


def _today() -> date:
    return date.today()


def parse_day(value: str) -> date:
    try:
        return datetime.strptime(str(value)[:10], DATE_FMT).date()
    except ValueError:
        raise SchoolError("Data inválida: use AAAA-MM-DD.")


def minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)


def hhmm(total: int) -> str:
    total = max(0, min(total, 23 * 60 + 59))
    return f"{total // 60:02d}:{total % 60:02d}"


# ================================================================== semeadura
def seed_defaults(conn: sqlite3.Connection) -> None:
    """Valores de exemplo (chamado UMA vez, na migração 5)."""
    year = _today().year
    defaults = dict(DEFAULT_SETTINGS, year_start=f"{year}-02-02", year_end=f"{year}-12-18")
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO school_settings (key, value) VALUES (?, ?)", (k, v))

    if not conn.execute("SELECT 1 FROM shifts").fetchone():
        for name, start, tol, end in (("Manhã", "07:00", 15, "12:20"), ("Tarde", "13:00", 15, "18:20"),
                                      ("Noite", "19:20", 15, "22:40")):
            conn.execute("INSERT INTO shifts (name, start_time, tolerance_min, end_time) VALUES (?, ?, ?, ?)",
                         (name, start, tol, end))
    shifts = {r[1]: r[0] for r in conn.execute("SELECT id, name FROM shifts")}
    # Noite 19:20 + 15 min = 19:35, o mesmo ENTRY_TOLERANCE usado até a Fase 0.
    night = shifts.get("Noite") or next(iter(shifts.values()))
    conn.execute("UPDATE school_settings SET value = ? WHERE key = 'default_shift_id' AND value = ''", (str(night),))

    existing = [r[0] for r in conn.execute(
        "SELECT DISTINCT TRIM(class_name) FROM students WHERE TRIM(COALESCE(class_name, '')) != ''")]
    for name in existing:   # turmas que já estavam digitadas nos alunos
        conn.execute("INSERT OR IGNORE INTO classes (name, shift_id) VALUES (?, ?)", (name, night))
    if not conn.execute("SELECT 1 FROM classes").fetchone():
        for name, shift in (("2ECA", "Manhã"), ("2ECT", "Tarde"), ("2ECR", "Noite")):   # padrão FIAP (exemplos)
            conn.execute("INSERT INTO classes (name, shift_id) VALUES (?, ?)", (name, shifts.get(shift, night)))

    if not conn.execute("SELECT 1 FROM calendar_entries WHERE source = 'escola'").fetchone():
        for y in (year, year + 1):
            conn.execute("INSERT INTO calendar_entries (start_date, end_date, kind, description, source) "
                         "VALUES (?, ?, 'FERIADO', 'Aniversário da cidade (exemplo: São Paulo)', 'escola')",
                         (f"{y}-01-25", f"{y}-01-25"))
            conn.execute("INSERT INTO calendar_entries (start_date, end_date, kind, description, source) "
                         "VALUES (?, ?, 'FERIADO', 'Revolução Constitucionalista (exemplo: estadual SP)', 'escola')",
                         (f"{y}-07-09", f"{y}-07-09"))
        conn.execute("INSERT INTO calendar_entries (start_date, end_date, kind, description, source) "
                     "VALUES (?, ?, 'RECESSO', 'Recesso escolar de julho (exemplo)', 'escola')",
                     (f"{year}-07-13", f"{year}-07-24"))


def ensure_national_holidays(conn: sqlite3.Connection) -> None:
    """Feriados nacionais do ano atual e do próximo; cada ano é semeado uma vez (pode ser editado depois)."""
    row = conn.execute("SELECT value FROM school_settings WHERE key = 'holiday_years'").fetchone()
    if row is None:
        return
    done = {y for y in (row[0] or "").split(",") if y}
    year = _today().year
    for y in (year, year + 1):
        if str(y) in done:
            continue
        for d, name in national_holidays(y):
            conn.execute("INSERT INTO calendar_entries (start_date, end_date, kind, description, source) "
                         "VALUES (?, ?, 'FERIADO', ?, 'nacional')", (d.isoformat(), d.isoformat(), name))
        done.add(str(y))
    conn.execute("UPDATE school_settings SET value = ? WHERE key = 'holiday_years'", (",".join(sorted(done)),))


# ================================================================== regras
def get_settings() -> Dict[str, str]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT key, value FROM school_settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def update_settings(values: Dict[str, Any]) -> Dict[str, str]:
    allowed = {"school_name", "school_days", "year_start", "year_end", "default_shift_id",
               "auto_exit_min_minutes", "absence_notice_after_minutes", "retention_photo_days",
               "retention_passages_days"}
    clean: Dict[str, str] = {}
    for k, v in values.items():
        if k not in allowed or v is None:
            continue
        v = str(v).strip()
        if k == "school_days":
            days = sorted({int(x) for x in re.findall(r"[1-7]", v)})
            if not days:
                raise SchoolError("Marque ao menos um dia da semana com aula.")
            v = ",".join(map(str, days))
        elif k in ("year_start", "year_end"):
            v = parse_day(v).isoformat()
        elif k == "default_shift_id":
            if not get_shift(_int(v, 0)):
                raise SchoolError("Turno padrão inexistente.")
        elif k == "auto_exit_min_minutes":
            if not 1 <= _int(v, -1) <= 600:
                raise SchoolError("Intervalo mínimo entre entrada e saída: de 1 a 600 minutos.")
        elif k == "absence_notice_after_minutes":
            if not 0 <= _int(v, -1) <= 600:
                raise SchoolError("Aviso automático de ausência: de 0 (só pela portaria) a 600 minutos.")
        elif k in ("retention_photo_days", "retention_passages_days"):
            if not 7 <= _int(v, -1) <= 3650:
                raise SchoolError("Prazos de retenção: de 7 a 3650 dias.")
        elif k == "school_name":
            if not 2 <= len(v) <= 80:
                raise SchoolError("Nome da escola: de 2 a 80 caracteres.")
        clean[k] = v
    merged = dict(get_settings(), **clean)
    if parse_day(merged["year_start"]) > parse_day(merged["year_end"]):
        raise SchoolError("O ano letivo termina antes de começar.")
    with get_db_connection() as conn:
        for k, v in clean.items():
            conn.execute("INSERT INTO school_settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
        conn.commit()
    return get_settings()


def update_consent_text(text: str) -> Dict[str, str]:
    text = (text or "").strip()
    if len(text) < 50:
        raise SchoolError("O termo está curto demais.")
    current = get_settings()
    if text == current["consent_text"]:
        return current
    version = str(_int(current["consent_version"], 1) + 1)
    with get_db_connection() as conn:
        for k, v in (("consent_text", text[:20000]), ("consent_version", version)):
            conn.execute("INSERT INTO school_settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
        conn.commit()
    return get_settings()


# ================================================================== turnos
def _check_shift(name: str, start: str, tolerance: int, end: str) -> Tuple[str, str, int, str]:
    name = (name or "").strip()
    if not 2 <= len(name) <= 40:
        raise SchoolError("Nome do turno: de 2 a 40 caracteres.")
    if not TIME_RE.match(start or "") or not TIME_RE.match(end or ""):
        raise SchoolError("Horários no formato HH:MM, ex.: 07:00.")
    if not 0 <= int(tolerance) <= 120:
        raise SchoolError("Tolerância: de 0 a 120 minutos.")
    if minutes(end) <= minutes(start) + int(tolerance):
        raise SchoolError("O fim das aulas precisa ser depois do início + tolerância.")
    return name, start, int(tolerance), end


def list_shifts(include_inactive: bool = True) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM classes c WHERE c.shift_id = s.id) AS classes "
            "FROM shifts s " + ("" if include_inactive else "WHERE s.active = 1 ") + "ORDER BY s.start_time").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active"] = bool(d["active"])
        d["deadline"] = hhmm(minutes(d["start_time"]) + d["tolerance_min"])
        out.append(d)
    return out


def get_shift(shift_id: int) -> Optional[Dict[str, Any]]:
    return next((s for s in list_shifts() if s["id"] == int(shift_id or 0)), None)


def create_shift(name: str, start: str, tolerance: int, end: str) -> Dict[str, Any]:
    name, start, tolerance, end = _check_shift(name, start, tolerance, end)
    with get_db_connection() as conn:
        try:
            cur = conn.execute("INSERT INTO shifts (name, start_time, tolerance_min, end_time) VALUES (?, ?, ?, ?)",
                               (name, start, tolerance, end))
        except sqlite3.IntegrityError:
            raise SchoolError("Já existe um turno com esse nome.")
        conn.commit()
        return get_shift(cur.lastrowid)


def update_shift(shift_id: int, name: str, start: str, tolerance: int, end: str, active: bool = True) -> Dict[str, Any]:
    if not get_shift(shift_id):
        raise SchoolError("Turno não encontrado.")
    name, start, tolerance, end = _check_shift(name, start, tolerance, end)
    if not active and str(shift_id) == get_settings()["default_shift_id"]:
        raise SchoolError("Este é o turno padrão (de quem não tem turma). Escolha outro padrão antes.")
    with get_db_connection() as conn:
        try:
            conn.execute("UPDATE shifts SET name = ?, start_time = ?, tolerance_min = ?, end_time = ?, active = ? "
                         "WHERE id = ?", (name, start, tolerance, end, int(bool(active)), shift_id))
        except sqlite3.IntegrityError:
            raise SchoolError("Já existe um turno com esse nome.")
        conn.commit()
    return get_shift(shift_id)


def delete_shift(shift_id: int) -> None:
    s = get_shift(shift_id)
    if not s:
        raise SchoolError("Turno não encontrado.")
    if s["classes"]:
        raise SchoolError(f"{s['classes']} turma(s) usam este turno. Mude o turno delas antes.")
    if str(shift_id) == get_settings()["default_shift_id"]:
        raise SchoolError("Este é o turno padrão (de quem não tem turma). Escolha outro padrão antes.")
    with get_db_connection() as conn:
        conn.execute("DELETE FROM calendar_entries WHERE shift_id = ?", (shift_id,))
        conn.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
        conn.commit()


# ================================================================== turmas
def list_classes() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.shift_id, c.active, s.name AS shift_name, "
            "(SELECT COUNT(*) FROM students st WHERE st.class_name = c.name COLLATE NOCASE) AS students "
            "FROM classes c LEFT JOIN shifts s ON s.id = c.shift_id ORDER BY c.name COLLATE NOCASE").fetchall()
    return [dict(r, active=bool(r["active"])) for r in rows]


def get_class(class_id: int) -> Optional[Dict[str, Any]]:
    return next((c for c in list_classes() if c["id"] == int(class_id)), None)


def get_class_by_name(name: str) -> Optional[Dict[str, Any]]:
    name = (name or "").strip().lower()
    return next((c for c in list_classes() if c["name"].lower() == name), None)


def _check_class(name: str, shift_id: int) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= 40:
        raise SchoolError("Nome da turma: de 1 a 40 caracteres.")
    if not get_shift(shift_id):
        raise SchoolError("Escolha um turno válido.")
    return name


def create_class(name: str, shift_id: int) -> Dict[str, Any]:
    name = _check_class(name, shift_id)
    with get_db_connection() as conn:
        try:
            cur = conn.execute("INSERT INTO classes (name, shift_id) VALUES (?, ?)", (name, int(shift_id)))
        except sqlite3.IntegrityError:
            raise SchoolError("Já existe uma turma com esse nome.")
        conn.commit()
        return get_class(cur.lastrowid)


def ensure_class(name: str) -> Optional[Dict[str, Any]]:
    """Turma usada no cadastro do aluno: se não existe, cria no turno padrão (compatível com o texto livre antigo)."""
    name = (name or "").strip()
    if not name:
        return None
    found = get_class_by_name(name)
    if found:
        return found
    return create_class(name, int(get_settings()["default_shift_id"] or 0))


def update_class(class_id: int, name: str, shift_id: int, active: bool = True) -> Dict[str, Any]:
    current = get_class(class_id)
    if not current:
        raise SchoolError("Turma não encontrada.")
    name = _check_class(name, shift_id)
    with get_db_connection() as conn:
        try:
            conn.execute("UPDATE classes SET name = ?, shift_id = ?, active = ? WHERE id = ?",
                         (name, int(shift_id), int(bool(active)), class_id))
        except sqlite3.IntegrityError:
            raise SchoolError("Já existe uma turma com esse nome.")
        if name != current["name"]:   # renomear leva os alunos junto
            conn.execute("UPDATE students SET class_name = ? WHERE class_name = ? COLLATE NOCASE",
                         (name, current["name"]))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='arena_classes'").fetchone():
                conn.execute("UPDATE arena_classes SET class_name=? WHERE class_name=? COLLATE NOCASE", (name, current['name']))
        conn.commit()
    return get_class(class_id)


def delete_class(class_id: int) -> None:
    c = get_class(class_id)
    if not c:
        raise SchoolError("Turma não encontrada.")
    if c["students"]:
        raise SchoolError(f"{c['students']} aluno(s) estão nesta turma. Mude a turma deles antes.")
    with get_db_connection() as conn:
        conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='arena_classes'").fetchone():
            conn.execute("DELETE FROM arena_classes WHERE class_name=? COLLATE NOCASE", (c['name'],))
        conn.commit()


# ================================================================== calendário
def list_calendar(year: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = ("SELECT e.*, s.name AS shift_name FROM calendar_entries e LEFT JOIN shifts s ON s.id = e.shift_id")
    params: list = []
    if year:
        sql += " WHERE e.end_date >= ? AND e.start_date <= ?"
        params = [f"{year}-01-01", f"{year}-12-31"]
    sql += " ORDER BY e.start_date, e.id"
    with get_db_connection() as conn:
        return [dict(r, kind_label=KINDS.get(r["kind"], r["kind"])) for r in conn.execute(sql, params)]


def _check_entry(start: str, end: str, kind: str, description: str, shift_id: Optional[int]):
    s, e = parse_day(start), parse_day(end or start)
    if e < s:
        raise SchoolError("A data final é antes da inicial.")
    if (e - s).days > 366:
        raise SchoolError("Período longo demais (máximo de 1 ano).")
    if kind not in KINDS:
        raise SchoolError("Tipo inválido.")
    description = (description or "").strip()
    if not description:
        raise SchoolError("Descreva o motivo (ex.: Feriado municipal).")
    if shift_id and not get_shift(shift_id):
        raise SchoolError("Turno inexistente.")
    return s.isoformat(), e.isoformat(), kind, description[:120], (int(shift_id) if shift_id else None)


def create_calendar_entry(start: str, end: str, kind: str, description: str,
                          shift_id: Optional[int] = None) -> Dict[str, Any]:
    values = _check_entry(start, end, kind, description, shift_id)
    with get_db_connection() as conn:
        cur = conn.execute("INSERT INTO calendar_entries (start_date, end_date, kind, description, shift_id, source) "
                           "VALUES (?, ?, ?, ?, ?, 'escola')", values)
        conn.commit()
        new_id = cur.lastrowid
    return next(e for e in list_calendar() if e["id"] == new_id)


def update_calendar_entry(entry_id: int, start: str, end: str, kind: str, description: str,
                          shift_id: Optional[int] = None) -> Dict[str, Any]:
    values = _check_entry(start, end, kind, description, shift_id)
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE calendar_entries SET start_date = ?, end_date = ?, kind = ?, description = ?, "
                           "shift_id = ? WHERE id = ?", values + (int(entry_id),))
        conn.commit()
        if not cur.rowcount:
            raise SchoolError("Item do calendário não encontrado.")
    return next(e for e in list_calendar() if e["id"] == int(entry_id))


def delete_calendar_entry(entry_id: int) -> Dict[str, Any]:
    entry = next((e for e in list_calendar() if e["id"] == int(entry_id)), None)
    if not entry:
        raise SchoolError("Item do calendário não encontrado.")
    with get_db_connection() as conn:
        conn.execute("DELETE FROM calendar_entries WHERE id = ?", (int(entry_id),))
        conn.commit()
    return entry


# ================================================================== dia letivo
class Calendar:
    """Retrato das regras (carregado uma vez) para decidir muitos dias/turnos sem ir ao banco."""

    def __init__(self, settings: Dict[str, str], shifts: List[Dict[str, Any]], entries: List[Dict[str, Any]],
                 classes: List[Dict[str, Any]]):
        self.settings = settings
        self.shifts = {s["id"]: s for s in shifts}
        self.entries = entries
        self.class_shift = {c["name"].lower(): c["shift_id"] for c in classes}
        self.default_shift = _int(settings.get("default_shift_id"), 0)
        if self.default_shift not in self.shifts and self.shifts:
            self.default_shift = next(iter(self.shifts))
        self.days = {int(x) for x in (settings.get("school_days") or "").split(",") if x.strip().isdigit()}

    @classmethod
    def load(cls) -> "Calendar":
        return cls(get_settings(), list_shifts(), list_calendar(), list_classes())

    def shift_for_class(self, class_name: str) -> Optional[Dict[str, Any]]:
        sid = self.class_shift.get((class_name or "").strip().lower(), self.default_shift)
        return self.shifts.get(sid) or self.shifts.get(self.default_shift)

    def day_status(self, day: date, shift_id: Optional[int] = None) -> Tuple[bool, str]:
        """(é dia letivo?, motivo quando não é)."""
        iso = day.isoformat()
        hits = [e for e in self.entries if e["start_date"] <= iso <= e["end_date"]
                and (e["shift_id"] is None or e["shift_id"] == shift_id)]
        extra = [e for e in hits if e["kind"] == "DIA_LETIVO"]
        if extra:
            return True, ""
        off = [e for e in hits if e["kind"] in NO_SCHOOL_KINDS]
        if off:
            return False, off[0]["description"]
        ys, ye = self.settings.get("year_start") or "", self.settings.get("year_end") or ""
        if (ys and iso < ys) or (ye and iso > ye):
            return False, "Fora do ano letivo"
        if day.isoweekday() not in self.days:
            return False, f"Sem aula ({WEEKDAYS[day.weekday()]})"
        return True, ""


def school_days_between(start: date, end: date, cal: Optional[Calendar] = None) -> List[date]:
    """Dias letivos (para algum turno) no intervalo, inclusive."""
    cal = cal or Calendar.load()
    out, d = [], start
    while d <= end:
        if any(cal.day_status(d, sid)[0] for sid in cal.shifts) or (not cal.shifts and cal.day_status(d)[0]):
            out.append(d)
        d += timedelta(days=1)
    return out


def year_map(year: int, cal: Optional[Calendar] = None) -> str:
    """Um caractere por dia do ano ("1" = letivo para algum turno, "0" = sem aula), a partir de 1º de janeiro."""
    cal = cal or Calendar.load()
    letivos = set(school_days_between(date(year, 1, 1), date(year, 12, 31), cal))
    days = (date(year, 12, 31) - date(year, 1, 1)).days + 1
    return "".join("1" if date(year, 1, 1) + timedelta(days=i) in letivos else "0" for i in range(days))


def deadline_for_class(class_name: str) -> str:
    """Horário-limite de entrada (início + tolerância) do turno da turma; "" se não houver turnos."""
    shift = Calendar.load().shift_for_class(class_name)
    return shift["deadline"] if shift else ""


# ================================================================== períodos letivos (bimestre, trimestre...)
DIVISIONS = {"bimestre": (4, "bimestre"), "trimestre": (3, "trimestre"), "semestre": (2, "semestre")}


def _split_year(ys: date, ye: date, n: int) -> List[Tuple[date, date]]:
    """Divide o ano letivo em n partes de mesmo número de dias (ponto de partida; as datas são editáveis)."""
    total = (ye - ys).days + 1
    out, start = [], ys
    for i in range(n):
        end = ys + timedelta(days=round(total * (i + 1) / n) - 1) if i < n - 1 else ye
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def academic_periods(today: Optional[date] = None) -> Dict[str, Any]:
    """Divisão do ano letivo usada nos relatórios. Padrão: bimestres, calculados a partir do ano letivo."""
    import json
    today = today or _today()
    s = get_settings()
    division = s.get("period_division") or "bimestre"
    n, label = DIVISIONS.get(division, DIVISIONS["bimestre"])
    ys, ye = parse_day(s["year_start"]), parse_day(s["year_end"])
    dates = _split_year(ys, ye, n)
    try:
        custom = json.loads(s.get("period_dates") or "[]")
        if isinstance(custom, list) and len(custom) == n:
            dates = [(parse_day(c[0]), parse_day(c[1])) for c in custom]
    except (ValueError, TypeError, IndexError, SchoolError):
        pass
    periods = [{"chave": f"p{i + 1}", "nome": f"{i + 1}º {label}", "inicio": a.isoformat(), "fim": b.isoformat(),
                "atual": a <= today <= b, "futuro": a > today} for i, (a, b) in enumerate(dates)]
    return {"divisao": division if division in DIVISIONS else "bimestre", "periodos": periods,
            "ano": {"inicio": ys.isoformat(), "fim": ye.isoformat()},
            "personalizado": bool(s.get("period_dates"))}


def update_academic_periods(division: str, dates: Optional[List[List[str]]] = None) -> Dict[str, Any]:
    import json
    if division not in DIVISIONS:
        raise SchoolError("Divisão inválida: bimestre, trimestre ou semestre.")
    n = DIVISIONS[division][0]
    value = ""
    if dates:
        if len(dates) != n:
            raise SchoolError(f"Informe as datas dos {n} períodos.")
        parsed = [(parse_day(a), parse_day(b)) for a, b in dates]
        for i, (a, b) in enumerate(parsed):
            if b < a:
                raise SchoolError(f"O {i + 1}º período termina antes de começar.")
            if i and a <= parsed[i - 1][1]:
                raise SchoolError(f"O {i + 1}º período começa antes do fim do anterior.")
        value = json.dumps([[a.isoformat(), b.isoformat()] for a, b in parsed])
    with get_db_connection() as conn:
        for k, v in (("period_division", division), ("period_dates", value)):
            conn.execute("INSERT INTO school_settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
        conn.commit()
    return academic_periods()
