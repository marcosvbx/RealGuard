"""Administração: usuários, perfis de acesso, auditoria e backup."""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.security import SESSION_COOKIE, audit, require
from app.engine import engine
from app.security import passwords
from database import access, backup
from database.migrations import initialize_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _bad(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


# ================================================================== usuários
class NewUserPayload(BaseModel):
    username: str
    full_name: str
    role: str
    password: str = ""


class UserPayload(BaseModel):
    full_name: str
    role: str
    active: bool = True


@router.get("/users")
def list_users(user=Depends(require("users.manage"))):
    """Inclui a situação do Telegram dos alertas (o administrador vincula por aqui, com QR Code)."""
    people = {p["id"]: p for p in access.security_people()}
    out = []
    for u in access.list_users():
        p = people.get(u["id"])
        u["alertas"] = {"pode": p is not None, "vinculado": bool(p and p["vinculado"]),
                        "recebe": bool(p and p["recebe"]), "decide": bool(p and p["decide"])}
        out.append(u)
    return out


@router.post("/users")
def create_user(payload: NewUserPayload, request: Request, user=Depends(require("users.manage"))):
    """Cria com senha provisória: a pessoa é obrigada a trocá-la no primeiro acesso."""
    temp = payload.password or passwords.generate_temporary_password()
    try:
        created = access.create_user(payload.username, payload.full_name, payload.role, temp,
                                     must_change_password=True)
    except access.AccessError as e:
        _bad(e)
    audit(request, user, "usuario_criado", "usuario", created["id"], f"{created['username']} ({created['role']})")
    return {"usuario": created, "senha_provisoria": temp}


@router.put("/users/{user_id}")
def update_user(user_id: int, payload: UserPayload, request: Request, user=Depends(require("users.manage"))):
    before = access.get_user(user_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if user_id == user["id"] and (not payload.active or payload.role != before["role"]):
        raise HTTPException(status_code=400, detail="Você não pode desativar nem mudar o perfil da sua própria conta.")
    try:
        after = access.update_user(user_id, payload.full_name, payload.role, payload.active)
    except access.AccessError as e:
        _bad(e)
    changes = []
    if before["role"] != after["role"]:
        changes.append(f"perfil {before['role']} -> {after['role']}")
    if before["active"] != after["active"]:
        changes.append("reativado" if after["active"] else "desativado")
    if before.get("full_name") != after.get("full_name"):
        changes.append("nome alterado")
    audit(request, user, "usuario_alterado", "usuario", user_id,
          f"{after['username']}: " + (", ".join(changes) or "sem mudanças"))
    return after


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, user=Depends(require("users.manage"))):
    target = access.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    temp = passwords.generate_temporary_password()
    access.set_password(user_id, temp, must_change=True)
    access.delete_user_sessions(user_id,
                                except_token=request.cookies.get(SESSION_COOKIE, "") if user_id == user["id"] else "")
    audit(request, user, "senha_redefinida", "usuario", user_id, target["username"])
    return {"senha_provisoria": temp}


# ================================================================== perfis
class RolePayload(BaseModel):
    name: str
    description: str = ""
    permissions: List[str] = []


@router.get("/roles")
def list_roles(user=Depends(require("roles.manage"))):
    return access.list_roles()


@router.get("/roles/options")
def role_options(user=Depends(require("users.manage"))):
    """Perfis para o seletor no cadastro de usuário (sem a matriz de permissões)."""
    return [{"chave": r["key"], "nome": r["name"], "descricao": r["description"], "padrao": r["is_system"],
             "ve_ocorrencias": "events.view" in r["permissions"]}
            for r in access.list_roles()]


@router.post("/roles")
def create_role(payload: RolePayload, request: Request, user=Depends(require("roles.manage"))):
    try:
        role = access.create_role(payload.name, payload.description, payload.permissions)
    except access.AccessError as e:
        _bad(e)
    audit(request, user, "perfil_criado", "perfil", role["key"],
          f"{role['name']}: {len(role['permissions'])} permissão(ões)")
    return role


@router.put("/roles/{key}")
def update_role(key: str, payload: RolePayload, request: Request, user=Depends(require("roles.manage"))):
    before = access.get_role(key)
    try:
        role = access.update_role(key, payload.name, payload.description, payload.permissions)
    except access.AccessError as e:
        _bad(e)
    added = sorted(set(role["permissions"]) - set(before["permissions"]))
    removed = sorted(set(before["permissions"]) - set(role["permissions"]))
    audit(request, user, "perfil_alterado", "perfil", key,
          f"{role['name']}: +{','.join(added) or '-'} / -{','.join(removed) or '-'}")
    return role


@router.delete("/roles/{key}")
def delete_role(key: str, request: Request, user=Depends(require("roles.manage"))):
    role = access.get_role(key)
    try:
        access.delete_role(key)
    except access.AccessError as e:
        _bad(e)
    audit(request, user, "perfil_excluido", "perfil", key, role["name"] if role else "")
    return {"status": "success"}


# ================================================================== auditoria
@router.get("/audit")
def audit_log(limit: int = 100, before_id: int = 0, action: str = "", username: str = "", days: int = 30,
              user=Depends(require("audit.view"))):
    return access.list_audit(limit=limit, before_id=before_id, action=action, username=username,
                             days=max(1, min(days, 400)))


# ================================================================== backup
@router.get("/backups")
def list_backups(user=Depends(require("backup.manage"))):
    import os
    return {"backups": backup.list_backups(), "pasta": backup.backup_dir(), "manter": backup.keep_count(),
            "automatico": os.getenv("BACKUP_AUTO", "true").strip().lower() in ("1", "true", "yes", "on"),
            "intervalo_horas": os.getenv("BACKUP_INTERVAL_HOURS", "24")}


@router.post("/backups")
def create_backup(request: Request, user=Depends(require("backup.manage"))):
    try:
        info = backup.create_backup("manual")
    except backup.BackupError as e:
        _bad(e)
    audit(request, user, "backup_criado", "backup", info["nome"])
    return info


@router.get("/backups/{name}/download")
def download_backup(name: str, request: Request, user=Depends(require("backup.manage"))):
    try:
        path = backup.backup_path(name)
    except backup.BackupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    audit(request, user, "backup_baixado", "backup", name)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/backups/{name}/restore")
def restore_backup(name: str, request: Request, user=Depends(require("backup.manage"))):
    try:
        result = backup.restore_backup(name)
    except backup.BackupError as e:
        _bad(e)
    # Backup de versão antiga: leva o banco restaurado até o schema atual.
    initialize_database(engine.settings.camera_id, engine.settings.camera_name)
    engine.reload_faces()
    engine.refresh_students()
    audit(request, user, "backup_restaurado", "backup", name,
          f"cópia de segurança antes: {result['backup_de_seguranca'] or '-'}")
    return {**result, "message": "Backup restaurado. Se a sua sessão cair, entre novamente."}


# ================================================================== dados fictícios
_sample_lock = __import__("threading").Lock()


@router.get("/ficticios")
def sample_state(user=Depends(require("sample.manage"))):
    from database import dados_ficticios
    from app import simulation
    return {**dados_ficticios.get_state(), "simulacao": simulation.get_state()}


class SimulationPayload(BaseModel):
    ligada: bool


@router.put("/ficticios/simulacao")
def sample_simulation(payload: SimulationPayload, request: Request, user=Depends(require("sample.manage"))):
    """Liga/desliga a rotina ao vivo dos alunos fictícios (passagens, avisos simulados, alertas de vez em quando)."""
    from datetime import datetime
    from app import simulation
    from database import dados_ficticios
    # ao religar, continua de AGORA (não "recupera" o tempo em que ficou desligada)
    st = simulation.set_state(ligada=payload.ligada, **({"ate": datetime.now().strftime(simulation.FMT)}
                                                        if payload.ligada else {}))
    audit(request, user, "simulacao_" + ("ligada" if payload.ligada else "desligada"), "dados", "ficticios")
    return {**dados_ficticios.get_state(), "simulacao": st,
            "message": "Simulação ao vivo ligada: os alunos fictícios seguem chegando e saindo." if payload.ligada
            else "Simulação ao vivo desligada: só a câmera registra passagens agora."}


class ClockPayload(BaseModel):
    horario: str = ""          # HH:MM; vazio com data vazia = relógio real
    data: str = ""             # AAAA-MM-DD; avança normalmente após o ajuste


@router.get("/relogio")
def clock_state(user=Depends(require("sample.manage"))):
    from config import settings as settings_module
    return settings_module.clock_info()


@router.put("/relogio")
def clock_set(payload: ClockPayload, request: Request, user=Depends(require("sample.manage"))):
    """
    Horário da demonstração (feira das 12h às 15h mostrando também a tarde e a noite): o RealGuard
    inteiro passa a considerar esse horário de hoje. A simulação completa (ou desfaz) na hora.
    """
    from app.simulation import SimulationLoop
    from config import settings as settings_module
    try:
        info = settings_module.set_demo_time(payload.horario, payload.data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        sim = SimulationLoop(engine).run_once()
    except Exception as e:                      # a rotina de 30 s tenta de novo
        logging.getLogger(__name__).warning(f"Simulação após ajuste do relógio: {e}")
        sim = {}
    adjusted = bool(info["ajuste_min"] or info["ajuste_dias"])
    audit(request, user, "relogio_ajustado", "relogio", "",
          f"demonstração {info['data']} {info['agora']} (real {info['data_real']} {info['real']})"
          if adjusted else "data e hora reais")
    msg = (f"Pronto: o sistema agora considera {info['data']} às {info['agora']} "
           f"(data e hora reais {info['data_real']} às {info['real']})." if adjusted
           else f"Voltou à data e hora reais ({info['data']} às {info['agora']}).")
    if sim.get("passagens"):
        msg += f" {sim['passagens']} passagem(ns) fictícia(s) completada(s)."
    if sim.get("removidas"):
        msg += f" {sim['removidas']} passagem(ns) fictícia(s) à frente removida(s)."
    return {**info, "message": msg}


def _sample_backup() -> str:
    try:
        return backup.create_backup("manual")["nome"]
    except backup.BackupError as e:
        _bad(Exception(f"Não consegui fazer o backup antes de mexer nos dados: {e}"))


@router.post("/ficticios/remover")
def sample_remove(request: Request, user=Depends(require("sample.manage"))):
    from database import dados_ficticios
    if not _sample_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Já existe uma operação com os dados fictícios em andamento.")
    try:
        name = _sample_backup()
        n = dados_ficticios.remove(engine.settings.camera_id)
    finally:
        _sample_lock.release()
    engine.reload_faces()
    engine.refresh_students()
    audit(request, user, "ficticios_removidos", "dados", "ficticios",
          f"{n['alunos']} alunos, {n['passagens']} passagens, {n['alertas']} alertas, {n['cameras']} câmeras; "
          f"backup {name}")
    return {**dados_ficticios.get_state(), "backup": name,
            "message": f"Removidos {n['alunos']} alunos fictícios. O que é real ficou intacto. Backup: {name}"}


@router.post("/ficticios/regenerar")
def sample_regenerate(request: Request, user=Depends(require("sample.manage"))):
    from database import dados_ficticios
    if not _sample_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Já existe uma operação com os dados fictícios em andamento.")
    try:
        name = _sample_backup()
        try:
            info = dados_ficticios.regenerate()
        except (dados_ficticios.SampleDataError, ValueError) as e:
            _bad(e)
    finally:
        _sample_lock.release()
    engine.reload_faces()
    engine.refresh_students()
    audit(request, user, "ficticios_regenerados", "dados", "ficticios",
          f"{info['alunos']} alunos, {info['de']} a {info['ate']}; backup {name}")
    return {**dados_ficticios.get_state(), "backup": name,
            "message": f"Dados fictícios recriados até hoje ({info['alunos']} alunos, {info['dias']} dias)."}
