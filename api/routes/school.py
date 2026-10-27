"""
Escola (Fase 1): turnos, turmas, calendário, regras de presença, retenção,
termo de consentimento e correções de frequência.
"""

from config.clock import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.security import audit, require, require_any
from app.engine import engine
from database import attendance, school

router = APIRouter(prefix="/api/school", tags=["school"])


def _bad(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


# ================================================================== leitura (telas que usam turma/turno)
@router.get("/classes")
def list_classes(user=Depends(require_any("school.manage", "students.view", "reports.view", "cameras.manage"))):
    return school.list_classes()


@router.get("/shifts")
def list_shifts(user=Depends(require_any("school.manage", "students.view", "reports.view"))):
    return school.list_shifts()


# ================================================================== turnos
class ShiftPayload(BaseModel):
    name: str
    start_time: str
    tolerance_min: int = 15
    end_time: str
    active: bool = True


@router.post("/shifts")
def create_shift(p: ShiftPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        s = school.create_shift(p.name, p.start_time, p.tolerance_min, p.end_time)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "turno_criado", "turno", s["id"], f"{s['name']} {s['start_time']}-{s['end_time']}")
    return s


@router.put("/shifts/{shift_id}")
def update_shift(shift_id: int, p: ShiftPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        s = school.update_shift(shift_id, p.name, p.start_time, p.tolerance_min, p.end_time, p.active)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "turno_alterado", "turno", shift_id,
          f"{s['name']} {s['start_time']}-{s['end_time']}, tolerância {s['tolerance_min']} min")
    return s


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int, request: Request, user=Depends(require("school.manage"))):
    try:
        school.delete_shift(shift_id)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "turno_excluido", "turno", shift_id)
    return {"status": "success"}


# ================================================================== turmas
class ClassPayload(BaseModel):
    name: str
    shift_id: int
    active: bool = True


@router.post("/classes")
def create_class(p: ClassPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        c = school.create_class(p.name, p.shift_id)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "turma_criada", "turma", c["id"], f"{c['name']} ({c['shift_name']})")
    return c


@router.put("/classes/{class_id}")
def update_class(class_id: int, p: ClassPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        c = school.update_class(class_id, p.name, p.shift_id, p.active)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "turma_alterada", "turma", class_id, f"{c['name']} ({c['shift_name']})")
    _refresh_engine()
    return c


@router.delete("/classes/{class_id}")
def delete_class(class_id: int, request: Request, user=Depends(require("school.manage"))):
    try:
        school.delete_class(class_id)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "turma_excluida", "turma", class_id)
    _refresh_engine()
    return {"status": "success"}


def _refresh_engine() -> None:
    """Renomear turma muda students.class_name: o cache do motor precisa reler."""
    from app.engine import engine
    engine.refresh_students()
    from app.arena import enabled, arena
    if enabled():
        from database.connection import get_db_connection
        with get_db_connection() as conn:
            classes = {r[0]:r[1] for r in conn.execute('SELECT camera_id,class_name FROM arena_classes')}
        with arena.lock:
            arena.classes = classes
            if arena.camera is not None:
                arena.set_class(arena.camera, classes.get(arena.camera, ''))


# ================================================================== calendário
class CalendarPayload(BaseModel):
    start_date: str
    end_date: str = ""
    kind: str = "FERIADO"
    description: str
    shift_id: Optional[int] = None


def _recompute_if_past(*entries) -> int:
    """Calendário mudou num dia que já passou: refaz a frequência daqueles dias (correções humanas ficam)."""
    today = date.today()
    starts = [school.parse_day(e["start_date"]) for e in entries if e]
    ends = [school.parse_day(e["end_date"]) for e in entries if e]
    if not starts or min(starts) > today:
        return 0
    return attendance.recompute_range(min(starts), min(max(ends), today))


@router.get("/calendar")
def list_calendar(year: int = 0, user=Depends(require("school.manage"))):
    y = year or date.today().year
    s = school.get_settings()
    return {"itens": school.list_calendar(year or None), "tipos": school.KINDS,
            "ano": y, "mapa": school.year_map(y), "ano_letivo": [s.get("year_start") or "", s.get("year_end") or ""]}


@router.post("/calendar")
def create_entry(p: CalendarPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        e = school.create_calendar_entry(p.start_date, p.end_date, p.kind, p.description, p.shift_id)
    except school.SchoolError as err:
        _bad(err)
    n = _recompute_if_past(e)
    audit(request, user, "calendario_criado", "calendario", e["id"],
          f"{e['kind_label']}: {e['description']} ({e['start_date']} a {e['end_date']})"
          + (f"; {n} dia(s) de frequência recalculado(s)" if n else ""))
    return e


@router.put("/calendar/{entry_id}")
def update_entry(entry_id: int, p: CalendarPayload, request: Request, user=Depends(require("school.manage"))):
    before = next((x for x in school.list_calendar() if x["id"] == entry_id), None)
    try:
        e = school.update_calendar_entry(entry_id, p.start_date, p.end_date, p.kind, p.description, p.shift_id)
    except school.SchoolError as err:
        _bad(err)
    _recompute_if_past(before, e)
    audit(request, user, "calendario_alterado", "calendario", entry_id,
          f"{e['kind_label']}: {e['description']} ({e['start_date']} a {e['end_date']})")
    return e


@router.delete("/calendar/{entry_id}")
def delete_entry(entry_id: int, request: Request, user=Depends(require("school.manage"))):
    try:
        e = school.delete_calendar_entry(entry_id)
    except school.SchoolError as err:
        _bad(err)
    _recompute_if_past(e)
    audit(request, user, "calendario_excluido", "calendario", entry_id, f"{e['description']} ({e['start_date']})")
    return {"status": "success"}


# ================================================================== regras e retenção
class SettingsPayload(BaseModel):
    school_name: Optional[str] = None
    school_days: Optional[str] = None
    year_start: Optional[str] = None
    year_end: Optional[str] = None
    default_shift_id: Optional[str] = None
    auto_exit_min_minutes: Optional[str] = None
    absence_notice_after_minutes: Optional[str] = None
    retention_photo_days: Optional[str] = None
    retention_passages_days: Optional[str] = None


_SETTING_KEYS = ("school_name", "school_days", "year_start", "year_end", "default_shift_id",
                 "auto_exit_min_minutes", "absence_notice_after_minutes", "retention_photo_days",
                 "retention_passages_days")


def _public_settings(s: dict) -> dict:
    return {k: s.get(k, "") for k in _SETTING_KEYS + ("consent_version",)}


@router.get("/settings")
def get_settings(user=Depends(require("school.manage"))):
    return _public_settings(school.get_settings())


@router.put("/settings")
def update_settings(p: SettingsPayload, request: Request, user=Depends(require("school.manage"))):
    before = school.get_settings()
    values = {k: getattr(p, k) for k in _SETTING_KEYS if getattr(p, k) is not None}
    try:
        after = school.update_settings(values)
        if engine.presence is not None:             # intervalo mínimo novo vale na hora (não em 60 s)
            engine.presence._gap_cache = (-1e9, 30 * 60.0)
    except school.SchoolError as e:
        _bad(e)
    changed = [f"{k}: {before.get(k)} -> {after.get(k)}" for k in values if before.get(k) != after.get(k)]
    audit(request, user, "regras_alteradas", "escola", "", "; ".join(changed) or "sem mudanças")
    return _public_settings(after)


# ================================================================== períodos letivos
class PeriodsPayload(BaseModel):
    divisao: str = "bimestre"
    datas: Optional[List[List[str]]] = None    # [[inicio, fim], ...]; vazio = dividir o ano em partes iguais


@router.get("/periods")
def get_periods(user=Depends(require_any("school.manage", "reports.view"))):
    return school.academic_periods()


@router.put("/periods")
def update_periods(p: PeriodsPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        out = school.update_academic_periods(p.divisao, p.datas)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "regras_alteradas", "escola", "", f"períodos letivos: {p.divisao}"
          + (" (datas personalizadas)" if p.datas else ""))
    return out


# ================================================================== termo de consentimento
class ConsentTextPayload(BaseModel):
    text: str


@router.get("/consent-text")
def consent_text(user=Depends(require_any("school.manage", "students.edit"))):
    s = school.get_settings()
    return {"texto": s["consent_text"], "versao": int(s["consent_version"] or 1), "escola": s["school_name"]}


@router.put("/consent-text")
def update_consent_text(p: ConsentTextPayload, request: Request, user=Depends(require("school.manage"))):
    try:
        s = school.update_consent_text(p.text)
    except school.SchoolError as e:
        _bad(e)
    audit(request, user, "termo_alterado", "escola", "", f"versão {s['consent_version']}")
    return {"texto": s["consent_text"], "versao": int(s["consent_version"]), "escola": s["school_name"]}


# ================================================================== frequência (correções)
attendance_router = APIRouter(prefix="/api/attendance", tags=["attendance"])


class MarkPayload(BaseModel):
    time: str
    reason: str = ""


class JustifyPayload(BaseModel):
    reason: str


def _day(value: str) -> date:
    try:
        return school.parse_day(value)
    except school.SchoolError as e:
        raise HTTPException(status_code=400, detail=str(e))


@attendance_router.get("/student/{student_id}")
def student_history(student_id: int, days: int = 30, user=Depends(require("students.view"))):
    return attendance.student_history(student_id, days)


@attendance_router.post("/{student_id}/{day}/present")
def mark_present(student_id: int, day: str, p: MarkPayload, request: Request,
                 user=Depends(require("attendance.mark"))):
    try:
        row = attendance.mark_present(student_id, _day(day), p.time, user, p.reason)
    except attendance.AttendanceError as e:
        _bad(e)
    audit(request, user, "presenca_manual", "aluno", student_id, f"{day} entrada {p.time}"
          + (f" — {p.reason.strip()}" if p.reason.strip() else ""))
    return row


@attendance_router.post("/{student_id}/{day}/justify")
def justify(student_id: int, day: str, p: JustifyPayload, request: Request,
            user=Depends(require("attendance.justify"))):
    try:
        row = attendance.justify(student_id, _day(day), p.reason, user)
    except attendance.AttendanceError as e:
        _bad(e)
    audit(request, user, "falta_justificada", "aluno", student_id, f"{day}: {p.reason.strip()}")
    return row


@attendance_router.post("/{student_id}/{day}/clear")
def clear(student_id: int, day: str, request: Request, user=Depends(require("attendance.justify"))):
    try:
        row = attendance.clear_manual(student_id, _day(day))
    except attendance.AttendanceError as e:
        _bad(e)
    audit(request, user, "correcao_desfeita", "aluno", student_id, day)
    return row
