"""Responsáveis do aluno: vários por aluno, parentesco, foto e o Telegram de cada um."""

import logging
import re
from typing import Optional
from urllib.parse import quote

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.security import audit, require
from app.engine import engine
from app.notifications.messages import presence_message, test_message
from database import guardians as gdb, repository, school

logger = logging.getLogger(__name__)

router = APIRouter(tags=["guardians"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
TG_OFF = "O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram."


class GuardianPayload(BaseModel):
    name: str = ""
    phone: str = ""
    relationship: str = ""
    primary: bool = False
    existing_id: Optional[int] = None      # reaproveitar um responsável já cadastrado (irmãos)


def _student(student_id: int) -> dict:
    s = repository.get_student(student_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")
    return s


def _guardian(guardian_id: int) -> dict:
    g = gdb.get(guardian_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Responsável não encontrado.")
    return g


def _bad(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


def _public(g: dict) -> dict:
    out = {k: g.get(k) for k in ("id", "name", "phone", "relationship", "is_primary", "telegram_vinculado",
                                  "tem_foto", "alunos")}
    out["is_primary"] = bool(out.get("is_primary"))
    return out


def _after_change() -> None:
    engine.refresh_students()


@router.get("/api/guardians/relationships")
def relationships(user=Depends(require("students.view"))):
    return gdb.RELATIONSHIPS


@router.get("/api/students/{student_id}/guardians")
def list_for_student(student_id: int, user=Depends(require("students.view", "guardians.view"))):
    _student(student_id)
    out = []
    for g in gdb.for_student(student_id):
        d = _public(g)
        d["outros_alunos"] = [s["name"] for s in gdb.students_of(g["id"]) if s["id"] != student_id]
        out.append(d)
    return out


@router.get("/api/guardians/search")
def search(phone: str, student_id: Optional[int] = None,
           user=Depends(require("students.edit", "guardians.view"))):
    """Responsável já cadastrado com esse celular (ex.: irmão já matriculado) — para reaproveitar."""
    return [{"id": g["id"], "name": g["name"], "phone": g["phone"], "telegram_vinculado": g["telegram_vinculado"],
             "alunos": [s["name"] for s in g["alunos"]]} for g in gdb.find_by_phone(phone, student_id)]


@router.post("/api/students/{student_id}/guardians")
def add(student_id: int, payload: GuardianPayload, request: Request,
        user=Depends(require("students.edit", "guardians.view"))):
    s = _student(student_id)
    try:
        if payload.existing_id:
            gid = _guardian(payload.existing_id)["id"]
        else:
            gid = gdb.create(payload.name, payload.phone, ficticio=bool(s.get("ficticio")))
        gdb.link(student_id, gid, payload.relationship, payload.primary)
    except gdb.GuardianError as e:
        _bad(e)
    _after_change()
    audit(request, user, "responsavel_adicionado", "aluno", student_id,
          f"{gdb.clean_relationship(payload.relationship)} · responsável #{gid}")
    return {"status": "success", "id": gid, "message": "Responsável adicionado."}


@router.put("/api/students/{student_id}/guardians/{guardian_id}")
def edit(student_id: int, guardian_id: int, payload: GuardianPayload, request: Request,
         user=Depends(require("students.edit", "guardians.view"))):
    _student(student_id)
    _guardian(guardian_id)
    try:
        gdb.update(guardian_id, payload.name, payload.phone)
        gdb.set_link(student_id, guardian_id, payload.relationship, payload.primary)
    except gdb.GuardianError as e:
        _bad(e)
    _after_change()
    audit(request, user, "responsavel_alterado", "aluno", student_id, f"responsável #{guardian_id}")
    return {"status": "success", "message": "Responsável salvo."}


@router.delete("/api/students/{student_id}/guardians/{guardian_id}")
def remove(student_id: int, guardian_id: int, request: Request,
           user=Depends(require("students.edit", "guardians.view"))):
    _student(student_id)
    if not gdb.unlink(student_id, guardian_id):
        raise HTTPException(status_code=404, detail="Este responsável não está ligado a este aluno.")
    _after_change()
    audit(request, user, "responsavel_removido", "aluno", student_id, f"responsável #{guardian_id}")
    return {"status": "success", "message": "Responsável removido deste aluno."}


# ------------------------------------------------------------------ foto
def _photo_jpeg(photo: Optional[UploadFile], use_camera: str) -> bytes:
    if str(use_camera).strip().lower() in ("1", "true", "on", "yes"):
        image = engine.snapshot()
        if image is None:
            raise HTTPException(status_code=503, detail="A câmera do sistema não está enviando imagem.")
    else:
        if photo is None:
            raise HTTPException(status_code=400, detail="Envie uma foto ou use a câmera do sistema.")
        content = photo.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Foto maior que 8 MB.")
        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=400, detail="Não foi possível ler a imagem enviada.")
    h, w = image.shape[:2]
    scale = min(1.0, gdb.PHOTO_MAX_SIDE / float(max(h, w)))
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Falha ao processar a foto.")
    return buf.tobytes()


@router.post("/api/guardians/{guardian_id}/photo")
def upload_photo(guardian_id: int, request: Request, use_camera: str = Form(""),
                 photo: Optional[UploadFile] = File(None),
                 user=Depends(require("students.edit", "guardians.view"))):
    """Foto para a portaria conferir quem veio buscar. Só imagem: não gera vetor facial."""
    _guardian(guardian_id)
    gdb.save_photo(guardian_id, _photo_jpeg(photo, use_camera))
    audit(request, user, "responsavel_foto", "responsavel", guardian_id)
    return {"status": "success", "message": "Foto do responsável salva."}


@router.get("/api/guardians/{guardian_id}/photo")
def get_photo(guardian_id: int, user=Depends(require("guardians.view"))):
    if not gdb.has_photo(guardian_id):
        raise HTTPException(status_code=404, detail="Sem foto.")
    return FileResponse(gdb.photo_path(guardian_id), media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-store"})


@router.delete("/api/guardians/{guardian_id}/photo")
def delete_photo(guardian_id: int, request: Request, user=Depends(require("students.edit", "guardians.view"))):
    _guardian(guardian_id)
    gdb.delete_photo(guardian_id)
    audit(request, user, "responsavel_foto_removida", "responsavel", guardian_id)
    return {"status": "success", "message": "Foto removida."}


# ------------------------------------------------------------------ Telegram de cada responsável
def _whatsapp_number(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if 10 <= len(digits) <= 11:
        digits = "55" + digits
    return digits


@router.get("/api/guardians/{guardian_id}/telegram-invite")
def invite(guardian_id: int, request: Request, user=Depends(require("guardians.telegram"))):
    g = _guardian(guardian_id)
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail=TG_OFF)
    bot = engine.notifier.get_bot_username()
    if not bot:
        raise HTTPException(status_code=503, detail="Não consegui falar com o Telegram agora (sem internet?).")
    token = gdb.ensure_link_token(guardian_id)
    link = f"https://t.me/{bot}?start={token}"
    kids = [s["name"] for s in gdb.students_of(guardian_id)]
    first = (g.get("name") or "").split(" ")[0] or "tudo bem"
    names = " e ".join(kids) if len(kids) <= 2 else ", ".join(kids[:-1]) + " e " + kids[-1]
    text = (f"Olá, {first}! Para receber no Telegram os avisos de entrada e saída de {names or 'seu filho(a)'} "
            f"na escola, abra o link e toque em Iniciar: {link}")
    number = _whatsapp_number(g.get("phone", ""))
    whatsapp = f"https://wa.me/{number}?text={quote(text)}" if number else f"https://wa.me/?text={quote(text)}"
    audit(request, user, "telegram_convite", "responsavel", guardian_id)
    return {"bot": bot, "link": link, "mensagem": text, "whatsapp": whatsapp, "vinculado": g["telegram_vinculado"]}


@router.post("/api/guardians/{guardian_id}/telegram-unlink")
def unlink_telegram(guardian_id: int, request: Request, user=Depends(require("guardians.telegram"))):
    _guardian(guardian_id)
    gdb.unlink_telegram(guardian_id)
    _after_change()
    audit(request, user, "telegram_desvinculado", "responsavel", guardian_id)
    return {"status": "success", "message": "Telegram desvinculado."}


@router.post("/api/guardians/{guardian_id}/test-telegram")
def test(guardian_id: int, request: Request, student_id: Optional[int] = None,
         user=Depends(require("guardians.telegram"))):
    g = _guardian(guardian_id)
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail=TG_OFF)
    if not g["telegram_vinculado"]:
        raise HTTPException(status_code=400, detail="Este responsável ainda não vinculou o Telegram.")
    kids = gdb.students_of(guardian_id)
    student = repository.get_student(student_id) if student_id else (repository.get_student(kids[0]["id"])
                                                                       if kids else None)
    student = student or {"name": "Aluno", "class_name": ""}
    deadline = school.deadline_for_class(student.get("class_name") or "") or engine.settings.entry_tolerance
    text = test_message(presence_message(student, engine.settings.camera_name, "ENTRY", deadline))
    ok, detail = engine.notifier.send_message(g["telegram_chat_id"], text, "HTML")
    if not ok:
        raise HTTPException(status_code=502, detail=detail)
    audit(request, user, "telegram_teste", "responsavel", guardian_id)
    return {"status": "success", "message": f"Teste enviado para {g.get('name') or 'o responsável'}."}
