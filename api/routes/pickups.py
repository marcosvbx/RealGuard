"""
Retiradas autorizadas (quem pode buscar o aluno num dia). Quem AUTORIZA é o
responsável, pelo bot do Telegram; a escola só vê (ficha do aluno) e pode
cancelar quando o responsável liga pedindo. Ver database/pickups.py.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from api.security import audit, require
from app.engine import engine
from database import pickups

logger = logging.getLogger(__name__)

router = APIRouter(tags=["retiradas"])


@router.get("/api/students/{student_id}/retiradas")
def list_for_student(student_id: int, user=Depends(require("students.view"))):
    """Próximas autorizações e o histórico dos últimos 30 dias."""
    return pickups.for_student(student_id)


@router.get("/api/retiradas/hoje")
def list_today(user=Depends(require("students.view"))):
    """
    Retiradas de hoje de todos os alunos (aguardando, autorizadas, recusadas, canceladas),
    com o horário da última saída do aluno na câmera hoje ("saida", vazio se ainda não saiu).
    """
    from database.connection import get_db_connection
    rows = pickups.for_day()
    exits = {}
    if rows:
        with get_db_connection() as conn:
            for r in conn.execute("SELECT student_id, MAX(created_at) AS t FROM presences WHERE direction = 'EXIT' "
                                  "AND date(created_at) = date('now','localtime') GROUP BY student_id"):
                exits[r["student_id"]] = str(r["t"])[11:16]
    return [dict({k: p.get(k) for k in ("id", "student_id", "student_name", "class_name", "person_name",
                                         "relationship", "situacao", "situacao_texto", "requested_by_name",
                                         "created_at")}, saida=exits.get(p.get("student_id"), ""))
            for p in rows]


@router.get("/api/retiradas/{pickup_id}/foto")
def photo(pickup_id: int, user=Depends(require("students.view"))):
    p = pickups.get(pickup_id)
    if p is None or not p["tem_foto"]:
        raise HTTPException(status_code=404, detail="Esta autorização não tem foto (ou ela já foi apagada).")
    return FileResponse(pickups.photo_path(pickup_id), media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-store"})


@router.post("/api/retiradas/{pickup_id}/cancelar")
def cancel(pickup_id: int, request: Request, user=Depends(require("students.edit"))):
    """Cancelada pela escola (o responsável pediu por telefone, por exemplo). Os responsáveis são avisados."""
    try:
        p = pickups.cancel(pickup_id, "escola: " + (user.get("full_name") or user.get("username") or ""))
    except pickups.PickupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        engine.guardian_bot.notify_school_cancel(p)
    except Exception as e:
        logger.warning(f"Aviso de cancelamento da retirada {pickup_id} não enviado: {e}")
    audit(request, user, "retirada_cancelada", "aluno", p["student_id"], f"{p['person_name']} em {p['day']} (pela escola)")
    return {"message": "Autorização cancelada. Os responsáveis foram avisados no Telegram.", "retirada": p}
