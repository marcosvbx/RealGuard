"""Ocorrências (alertas de segurança) e a decisão humana sobre elas."""

import logging
import os
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.security import audit, require
from app.engine import engine
from app.notifications.messages import CATEGORIES
from database import repository
from database.repository import EventStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


class Decision(str, Enum):
    VALIDATED_TRUE = EventStatus.VALIDATED_TRUE.value     # confirmar a ocorrência
    VALIDATED_FALSE = EventStatus.VALIDATED_FALSE.value   # descartar (falso alarme)


class ValidationPayload(BaseModel):
    # Só as duas decisões humanas são aceitas (qualquer outro valor = 422).
    # Quem decidiu vem da sessão, nunca do corpo da requisição.
    status: Decision
    category: str = ""      # confirmação: o que era (ver CATEGORIES)
    note: str = ""


@router.get("")
def list_events(status: str = "PENDING", days: int = 0, limit: int = 100, user=Depends(require("events.view"))):
    """PENDING inclui todas as pendências. No histórico, days/limit delimitam a consulta."""
    return repository.list_events(status=status.upper(), since_days=max(0, min(days, 90)),
                                  limit=max(1, min(limit, 500)))


@router.get("/pending")
def get_pending_events(user=Depends(require("events.view"))):
    """Alertas ainda não validados por um humano."""
    return repository.list_pending_events()


@router.get("/{event_id}/snapshot")
def event_snapshot(event_id: int, user=Depends(require("events.view"))):
    path = repository.event_snapshot_path(event_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Este alerta não tem foto.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


@router.post("/{event_id}/validate")
def validate_event(event_id: int, payload: ValidationPayload, request: Request,
                   user=Depends(require("events.decide"))):
    """
    Decisão humana sobre um alerta pendente (confirmar ou descartar). Só vale uma vez.
    Confirmada: grava o tipo e a observação e manda o relatório completo (com foto) à segurança.
    Descartada: manda só uma linha avisando que já foi tratado.
    """
    from app import security_alerts
    category = (payload.category or "").strip()
    if payload.status == Decision.VALIDATED_TRUE and category not in CATEGORIES:
        category = "outro"
    result, sent = security_alerts.decide(engine, event_id, payload.status.value, user, category, payload.note, via="painel")
    if result == repository.DECIDE_INVALID:
        raise HTTPException(status_code=400, detail="Decisão inválida: use VALIDATED_TRUE ou VALIDATED_FALSE.")
    if result == repository.DECIDE_NOT_FOUND:
        raise HTTPException(status_code=404, detail="Alerta não encontrado.")
    if result == repository.DECIDE_ALREADY:
        raise HTTPException(status_code=400, detail="Este alerta já foi decidido.")
    detail = payload.status.value + (f" · {CATEGORIES.get(category, category)}" if category else "")
    audit(request, user, "ocorrencia_decidida", "evento", event_id, detail)
    return {"message": "Event validated successfully", "event_id": event_id, "status": payload.status.value,
            "telegram": sent}


@router.delete("/{event_id}")
def delete_event(event_id: int, request: Request, user=Depends(require("events.delete"))):
    """Remove do histórico uma ocorrência já decidida (alarme falso, teste). Fica registrado na auditoria."""
    try:
        e = repository.delete_event(event_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    if not e:
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada.")
    audit(request, user, "ocorrencia_excluida", "ocorrencia", event_id,
          f"{e['event_type']} · {e['status']} · {e['created_at']}")
    return {"status": "success", "message": "Ocorrência excluída do histórico."}


@router.get("/history")
def history(days: int = 30, user=Depends(require("events.view"))):
    """Histórico (decididos e descartados automaticamente) com quem decidiu, por onde e em quanto tempo."""
    rows = repository.list_events(status="RESOLVED", since_days=max(1, min(days, 365)), limit=1000)
    from datetime import datetime
    for r in rows:
        r["tempo_min"] = None
        try:
            if r.get("validated_at"):
                d = (datetime.strptime(r["validated_at"][:19], "%Y-%m-%d %H:%M:%S")
                     - datetime.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                r["tempo_min"] = round(d, 1) if d >= 0 else None
        except (TypeError, ValueError):
            pass
    return rows


@router.get("/categories")
def categories(user=Depends(require("events.view"))):
    return [{"chave": k, "nome": v} for k, v in CATEGORIES.items()]
