import csv
import io
from config.clock import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from api.security import audit, has, require, require_any
from app.engine import engine
from app.school import xlsx
from database import report_export, reports, school

router = APIRouter(prefix="/api", tags=["reports"])


def _period(value: str) -> str:
    # sem Query(pattern=...) para funcionar também no FastAPI 0.95 da AIBOX
    return value if value in reports.PERIODS or value in reports.SCHOOL_PERIODS else "week"


def _filters(start: str, end: str, shift_id: Optional[int], class_name: str, student_id: Optional[int]) -> dict:
    # Filtros da Fase 1: período livre (start/end), turno, turma e aluno.
    try:
        if start or end:
            reports.resolve_range("week", start, end)
    except school.SchoolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"start": start, "end": end, "shift_id": shift_id or None, "class_name": (class_name or "").strip(),
            "student_id": student_id or None}


@router.get("/overview")
def overview(user=Depends(require_any("overview.view", "events.view"))):
    # Também alimenta "Ausentes hoje" na tela de Ocorrências.
    return reports.get_overview(engine.settings.entry_tolerance)


@router.get("/reports")
def report(period: str = "week", start: str = "", end: str = "", shift_id: Optional[int] = None,
           class_name: str = "", student_id: Optional[int] = None, user=Depends(require("reports.view"))):
    return reports.get_report(_period(period), engine.settings.entry_tolerance,
                              **_filters(start, end, shift_id, class_name, student_id))


@router.get("/reports/export.csv")
def export_csv(request: Request, period: str = "week", start: str = "", end: str = "",
               shift_id: Optional[int] = None, class_name: str = "", student_id: Optional[int] = None,
               user=Depends(require("reports.view", "reports.export"))):
    f = _filters(start, end, shift_id, class_name, student_id)
    s, e = reports.resolve_range(_period(period), f["start"], f["end"])
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows(
        reports.export_rows(_period(period), engine.settings.entry_tolerance, **f))
    audit(request, user, "relatorio_exportado", "relatorio", f"{s} a {e}",
          ", ".join(f"{k}={v}" for k, v in f.items() if v and k not in ("start", "end")))
    name = f"realguard_{s.isoformat()}_a_{e.isoformat()}.csv"
    # BOM para o Excel abrir acentos corretamente
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


# ================================================================== exportação personalizada
@router.get("/reports/exportar/opcoes")
def export_options(user=Depends(require("reports.view", "reports.export"))):
    """O que a janela "Exportar" oferece: modelos prontos, colunas de cada formato e filtros."""
    return report_export.catalog(has(user, "guardians.view"))


def _cell_csv(value, kind: str) -> str:
    if value is None:
        return ""
    if kind == "num1" and isinstance(value, (int, float)):
        return f"{value:.1f}".replace(".", ",")      # Excel em português: vírgula decimal
    return str(value)


@router.get("/reports/exportar")
def export_custom(request: Request, modelo: str = "frequencia", colunas: str = "", formato: str = "csv",
                  situacao: str = "todos", status: str = "", ordem: str = "turma", ficticios: str = "1",
                  pronto: str = "", period: str = "week", start: str = "", end: str = "",
                  shift_id: Optional[int] = None, class_name: str = "", student_id: Optional[int] = None,
                  user=Depends(require("reports.view", "reports.export"))):
    f = _filters(start, end, shift_id, class_name, student_id)
    contacts = has(user, "guardians.view")
    try:
        cols, rows, s, e = report_export.build(
            modelo, [c.strip() for c in colunas.split(",") if c.strip()], contacts, _period(period), f["start"],
            f["end"], f["shift_id"], f["class_name"], f["student_id"], situacao=situacao,
            status=[x.strip() for x in status.split(",") if x.strip()], ordem=ordem,
            with_sample=ficticios.strip() not in ("0", "false", "nao", "não"))
    except report_export.ExportError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    preset = next((p for p in report_export.PRESETS if p["chave"] == pronto), None)
    title = preset["nome"] if preset else report_export.MODELS[modelo]["nome"]
    slug = (pronto or modelo).replace("_", "-")
    name = f"realguard_{slug}_{s.isoformat()}_a_{e.isoformat()}"
    recorte = ", ".join(x for x in (
        f"turno {next((sh['name'] for sh in school.list_shifts() if sh['id'] == f['shift_id']), '')}"
        if f["shift_id"] else "", f"turma {f['class_name']}" if f["class_name"] else "",
        "um aluno" if f["student_id"] else "") if x) or "escola toda"
    audit(request, user, "relatorio_exportado", "relatorio", f"{s} a {e}",
          f"{title} · {formato} · {len(rows)} linha(s) · {recorte}"
          + (" · com contatos" if any(c[0] in ("responsavel", "celular") for c in cols) else ""))
    if formato == "xlsx":
        kinds = {"int": "int", "num1": "num1"}
        sheet = xlsx.Sheet(title[:31], [xlsx.Column(t, max(10, min(36, len(t) + 4)), kinds.get(k, "text"))
                                        for _, t, k in cols], rows, "3987E5")
        about = xlsx.Sheet("Sobre", [], [], "0B2447", notes=[
            f"RealGuard · {title}",
            f"Período: {s.strftime('%d/%m/%Y')} a {e.strftime('%d/%m/%Y')} · Recorte: {recorte}",
            f"Gerado em {datetime.now():%d/%m/%Y %H:%M} por {user.get('full_name') or user['username']}",
            "",
            "## Como ler",
            "• Frequência = presenças ÷ presenças esperadas. Falta justificada não conta contra.",
            "• Situação: abaixo de 75% (mínimo da LDB), atenção de 75% a 80%, regular a partir de 80%.",
            "• Pontualidade = presenças sem atraso ÷ presenças.",
            "",
            "## Privacidade (LGPD)",
            "Dados pessoais de estudantes" + (" e contatos dos responsáveis" if contacts else "")
            + ": guarde com cuidado e apague as cópias que não forem mais usadas.",
        ])
        content = xlsx.write_xlsx([sheet, about], f"RealGuard · {title}")
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={name}.xlsx"})
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([t for _, t, _ in cols])
    for r in rows:
        w.writerow([_cell_csv(v, cols[i][2]) for i, v in enumerate(r)])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})
