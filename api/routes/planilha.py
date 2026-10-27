"""
Planilha da base (Escola → Planilha da base): exportar em .xlsx e importar para atualizar em massa.
Regras em app/school/planilha.py. A importação tem duas etapas: prévia (aplicar=0) e gravar (aplicar=1),
com o mesmo arquivo: o servidor refaz a prévia na hora de gravar, então nada desatualizado é gravado.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from api.security import audit, has, require
from app.engine import engine
from app.school import planilha

router = APIRouter(prefix="/api/base", tags=["planilha"])

MAX_UPLOAD = 10 * 1024 * 1024
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/planilha.xlsx")
def export_planilha(request: Request, user=Depends(require("base.export"))):
    with_guardians = has(user, "guardians.view")
    content = planilha.export_base(include_guardians=with_guardians)
    audit(request, user, "planilha_base_exportada", "base", "",
          "com responsáveis" if with_guardians else "sem responsáveis")
    name = f"realguard_base_{datetime.now():%Y-%m-%d}.xlsx"
    return Response(content, media_type=XLSX_TYPE, headers={"Content-Disposition": f"attachment; filename={name}"})


@router.post("/importar")
async def import_planilha(request: Request, arquivo: UploadFile = File(...), aplicar: str = Form("0"),
                          user=Depends(require("base.import"))):
    content = await arquivo.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Planilha grande demais (máximo 10 MB).")
    if not content:
        raise HTTPException(status_code=400, detail="Escolha a planilha .xlsx.")
    try:
        plan = planilha.build_plan(content, include_guardians=has(user, "guardians.view"))
    except planilha.PlanilhaError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out = plan.as_dict()
    if str(aplicar).strip() not in ("1", "true", "sim"):
        out["gravado"] = False
        return out
    if not out["resumo"]["gravar"]:
        out["gravado"] = False
        out["message"] = "Nada para gravar: a planilha está igual ao sistema."
        return out
    try:
        counts = planilha.apply_plan(plan)
    except planilha.PlanilhaError as e:
        raise HTTPException(status_code=400, detail=str(e))
    engine.reload_faces()        # nomes e turmas do cache do reconhecimento
    errors = out["resumo"]["total"]["erro"]
    detail = ", ".join(f"{a}: {t['novo']} novo(s), {t['alterado']} alterado(s)" for a, t in out["resumo"]["abas"].items()
                       if t["novo"] or t["alterado"])
    audit(request, user, "planilha_base_importada", "base", arquivo.filename or "",
          detail + (f" · {errors} linha(s) com erro puladas" if errors else ""))
    out["gravado"] = True
    out["message"] = (f"Pronto: {counts['novo']} cadastro(s) novo(s) e {counts['alterado']} atualizado(s)"
                      + (f". {errors} linha(s) com erro ficaram de fora." if errors else "."))
    return out
