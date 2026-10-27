"""
Indicadores agregados (somente leitura).

A antiga rota POST /api/dashboard/events/manual foi removida na Fase 0:
permitia a qualquer pessoa na rede criar alertas falsos e nenhuma tela a
usava. Alertas nascem só do pipeline de visão.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.security import require
from database import repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def get_stats(user=Depends(require("overview.view"))):
    try:
        return repository.get_dashboard_stats()
    except Exception as e:
        logger.error(f"Erro em get_stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error retrieving stats.")


@router.get("/events/recent")
def get_recent_events(limit: int = Query(50, ge=1, le=1000), user=Depends(require("events.view"))):
    try:
        return repository.get_recent_events(limit=limit)
    except Exception as e:
        logger.error(f"Erro em get_recent_events: {e}")
        raise HTTPException(status_code=500, detail="Internal server error retrieving recent events.")


@router.get("/events/by-hour")
def get_events_by_hour(user=Depends(require("events.view"))):
    try:
        return repository.get_events_last_24h_by_hour()
    except Exception as e:
        logger.error(f"Erro em get_events_by_hour: {e}")
        raise HTTPException(status_code=500, detail="Internal server error retrieving events by hour.")


@router.get("/atividades")
def activities(tipo: str = "", turma: str = "", q: str = "", limit: int = Query(15, ge=1, le=300),
               user=Depends(require("overview.view"))):
    """Atividades recentes da Visão geral, filtradas no banco (tipo, turma e busca)."""
    from config.clock import date
    from database import reports
    return {"itens": reports.recent_activity(limit, tipo=tipo, turma=turma.strip(), q=q[:60], day=date.today()),
            "contagem": reports.activity_counts(turma.strip())}
