from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from api.security import require, require_any, audit
from app.arena import arena, enabled
from database.connection import get_db_connection

router = APIRouter(prefix='/api/arena', tags=['arena'])


def check():
    if not enabled():
        raise HTTPException(404, 'Modo Arena não está ativo.')


@router.get('/room')
def room(user=Depends(require_any('overview.view','live.view'))):
    check()
    return arena.snapshot()


class NavigationPayload(BaseModel):
    page: str


class InterfaceErrorPayload(BaseModel):
    message: str


@router.post('/navigation')
def navigation(payload: NavigationPayload, request: Request,
               user=Depends(require_any('overview.view','live.view','events.view','students.view','school.view'))):
    check()
    pages = {'geral': 'Visão geral', 'ocorrencias': 'Ocorrências', 'vivo': 'Ao vivo',
             'alunos': 'Alunos', 'escola': 'Escola'}
    if payload.page not in pages:
        raise HTTPException(400, 'Página inválida.')
    audit(request, user, 'arena_navegacao', 'pagina', payload.page, pages[payload.page])
    return {'ok': True}


@router.post('/interface-error')
def interface_error(payload: InterfaceErrorPayload, request: Request,
                    user=Depends(require_any('overview.view','live.view','events.view','students.view','school.view'))):
    check()
    message = payload.message.strip()[:250]
    if not message:
        raise HTTPException(400, 'Erro vazio.')
    audit(request, user, 'arena_erro_interface', 'interface', '', message)
    return {'ok': True}


class ClassPayload(BaseModel):
    class_name: str


@router.get('/cameras/classes')
def camera_classes(user=Depends(require('cameras.manage'))):
    check()
    with get_db_connection() as conn:
        return {str(row[0]): row[1] for row in conn.execute('SELECT camera_id,class_name FROM arena_classes')}


@router.put('/cameras/{camera_id}/class')
def camera_class(camera_id: int, payload: ClassPayload, request: Request, user=Depends(require('cameras.manage'))):
    check()
    from database import repository
    if not repository.get_camera(camera_id):
        raise HTTPException(404, 'Câmera não encontrada.')
    name = payload.class_name.strip()[:100]
    if not name:
        raise HTTPException(400, 'Informe a turma da sala.')
    from database import school
    if not any(c['name'] == name and c['active'] for c in school.list_classes()):
        raise HTTPException(400, 'Selecione uma turma ativa cadastrada em Escola.')
    with get_db_connection() as conn:
        conn.execute('INSERT OR REPLACE INTO arena_classes(camera_id,class_name) VALUES(?,?)',(camera_id,name))
        conn.commit()
    arena.set_class(camera_id,name)
    audit(request,user,'arena_turma','camera',camera_id,name)
    return {'class_name': name}


@router.get('/incidents')
def incidents(user=Depends(require('events.view'))):
    check()
    with arena.lock:
        return list(reversed(list(arena.incidents.values())))


class DecisionPayload(BaseModel):
    status: str


@router.get('/incidents/{incident_id}/photo')
def incident_photo(incident_id: int, session: str, user=Depends(require('events.view'))):
    check()
    from fastapi.responses import Response
    with arena.lock:
        if session != arena.session or incident_id not in arena.incidents or arena.incidents[incident_id]['status'] != 'PENDING':
            raise HTTPException(404, 'Captura não encontrada nesta sessão.')
        photo = arena.photos.get(incident_id)
    if photo is None:
        with get_db_connection() as conn:
            row = conn.execute('SELECT jpeg FROM arena_photos WHERE session=? AND incident_id=?', (session,incident_id)).fetchone()
        photo = row[0] if row else None
    if not photo:
        raise HTTPException(404, 'Ocorrência sem captura disponível.')
    return Response(content=photo, media_type='image/jpeg', headers={'Cache-Control':'no-store'})


@router.put('/incidents/{incident_id}')
def decide(incident_id: int, payload: DecisionPayload, request: Request, user=Depends(require('events.decide'))):
    check()
    if payload.status not in ('CONFIRMED', 'DISCARDED'):
        raise HTTPException(400, 'Decisão inválida.')
    try:
        result = arena.resolve(incident_id, payload.status, user.get('username', user.get('usuario', 'operador')))
    except KeyError:
        raise HTTPException(404, 'Ocorrência não encontrada nesta sessão.')
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(request, user, 'arena_ocorrencia_resolvida', 'arena', incident_id, payload.status)
    return result


@router.get('/history')
def history(limit: int=100, include_metrics: bool=True, user=Depends(require('events.view'))):
    check()
    import json
    with get_db_connection() as conn:
        rows=conn.execute("SELECT payload FROM arena_events WHERE session=? AND (? OR json_extract(payload, '$.kind') != 'METRICS') ORDER BY id DESC LIMIT ?",
                          (arena.session,include_metrics,max(1,min(1000,limit)))).fetchall()
    return [json.loads(r[0]) for r in rows]
