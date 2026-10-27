"""Cadastro e gestão de alunos (dados + rosto + Telegram)."""

import logging
import re
from urllib.parse import quote
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from api.security import audit, has, require, require_any
from app.engine import engine
from app.notifications.messages import absence_message, early_exit_message, presence_message, test_message
import json

from app.school.rm import display_rm
from database import guardians as gdb, notifications as nlog, repository, school

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/students", tags=["students"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _save_primary_photo(student_id, image):
    from database import student_photos
    h, w = image.shape[:2]
    if max(h, w) > 480:
        image = cv2.resize(image, (round(w*480/max(h,w)), round(h*480/max(h,w))))
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(500, 'Não foi possível salvar a foto principal.')
    student_photos.save(student_id, data.tobytes())


@router.get('/{student_id}/photo')
def primary_photo(student_id: int, user=Depends(require_any('students.view','live.view','overview.view'))):
    from database import student_photos
    from fastapi.responses import Response
    data = student_photos.get(student_id)
    if data is None:
        raise HTTPException(404, 'Sem foto principal autorizada. Adicione uma referência facial e marque salvar foto principal.')
    return Response(data, media_type='image/jpeg', headers={'Cache-Control':'no-store'})
CHAT_ID_RE = re.compile(r"^-?\d{5,20}$")


def _clean_chat_id(value: str) -> str:
    value = (value or "").strip()
    if value and not CHAT_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="Telegram Chat ID inválido: use só números (ex.: 123456789).",
        )
    return value


def _clean_phone(value: str) -> str:
    """Celular do responsável: aceita qualquer formatação; guarda como digitado se tiver 10-13 dígitos."""
    value = (value or "").strip()
    digits = re.sub(r"\D", "", value)
    if value and not 10 <= len(digits) <= 13:
        raise HTTPException(status_code=400, detail="Celular inválido: use DDD + número, ex.: (11) 98765-4321.")
    return value


def _decode_image(content: bytes) -> Optional[np.ndarray]:
    return cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)


def _embedding_from_request(photo: Optional[UploadFile], use_camera: str, image_out=None) -> np.ndarray:
    """
    Obtém o vetor facial a partir de uma foto enviada OU do frame atual da
    câmera do sistema (útil na AIBOX, onde o navegador não libera a webcam
    em http://IP). Falha com mensagem clara se não houver exatamente 1 rosto.
    """
    recognizer = engine.face_recognizer()
    if recognizer is None:
        raise HTTPException(
            status_code=503,
            detail=engine.notice or "Reconhecimento facial ainda não está pronto. Aguarde alguns segundos.",
        )

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
        image = _decode_image(content)
        if image is None:
            raise HTTPException(status_code=400, detail="Não foi possível ler a imagem enviada.")

    embedding, faces = recognizer.extract_registration_embedding(image)
    if faces == 0:
        raise HTTPException(
            status_code=400,
            detail="Nenhum rosto detectado. Use uma foto nítida, de frente e bem iluminada.",
        )
    if faces > 1:
        raise HTTPException(status_code=400, detail=f"{faces} rostos detectados. Use uma foto com apenas um rosto.")
    if image_out is not None:
        image_out.append(image)
    return embedding


def _rm(value: str) -> str:
    """RM no padrão FIAP (RM + 6 números); "566217" vira "RM566217"."""
    from app.school.rm import RMError, normalize_rm
    try:
        return normalize_rm(value)
    except RMError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _class_name(value: str) -> str:
    """Turma do aluno (Fase 1): tem de existir no cadastro de turmas; nome novo é criado no turno padrão."""
    try:
        found = school.ensure_class(value)
    except school.SchoolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return found["name"] if found else ""


def _deadline(student: dict) -> str:
    """Horário-limite de entrada do turno do aluno (mensagens de atraso/ausência)."""
    return school.deadline_for_class(student.get("class_name") or "") or engine.settings.entry_tolerance


def _consent_given(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes", "sim")


def _parse_guardians(raw: str) -> list:
    """Campo 'guardians' do cadastro: JSON [{name, phone, relationship, existing_id?}, ...]."""
    if not (raw or "").strip():
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Lista de responsáveis inválida.")
    out = []
    for g in items if isinstance(items, list) else []:
        if not isinstance(g, dict):
            continue
        name, phone = str(g.get("name") or "").strip(), str(g.get("phone") or "").strip()
        existing = g.get("existing_id")
        if not (name or phone or existing):
            continue
        try:
            phone = gdb.clean_phone(phone)
        except gdb.GuardianError as e:
            raise HTTPException(status_code=400, detail=f"{name or 'Responsável'}: {e}")
        out.append({"name": name, "phone": phone, "relationship": gdb.clean_relationship(g.get("relationship")),
                    "existing_id": int(existing) if str(existing or "").isdigit() else None})
    return out[:6]


def _chats(student: dict, kind: str = "") -> list:
    """
    Chats de todos os responsáveis vinculados (cai no campo antigo do aluno se a tabela nova
    estiver vazia). kind="ausencia": só quem não desligou esse aviso no bot (🔔 O que receber).
    """
    try:
        chats = gdb.chats_for_notice(student["id"], kind) if kind else gdb.chat_ids_for_student(student["id"])
        if kind and not chats and gdb.chat_ids_for_student(student["id"]):
            return []           # todos os vinculados desligaram este aviso
    except Exception:
        chats = []
    legacy = (student.get("telegram_chat_id") or "").strip()
    return chats or ([legacy] if legacy else [])


def _get_or_404(student_id: int) -> dict:
    student = repository.get_student(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")
    return student


@router.get("")
def list_students(user=Depends(require("students.view"))):
    students = repository.list_students()
    from app.arena import enabled
    if enabled():
        students = [s for s in students if not s.get("ficticio")]
    see = has(user, "guardians.view")
    by_student = gdb.for_students() if see else {}
    from database import pickups
    upcoming = pickups.upcoming_by_student()
    for s in students:
        # Retiradas autorizadas pelos responsáveis (hoje em diante) — a portaria confere na ficha.
        s["retiradas"] = [{"id": p["id"], "day": p["day"], "person_name": p["person_name"],
                           "relationship": p["relationship"], "situacao": p["situacao"], "tem_foto": p["tem_foto"]}
                          for p in upcoming.get(s["id"], [])]
        gs = by_student.get(s["id"], [])
        s["telegram_vinculados"] = len([c for c in (s.get("telegram_chats") or "").split(",") if c])
        s.pop("telegram_chats", None)
        if see:
            s["responsaveis"] = [{"id": g["id"], "name": g["name"], "phone": g["phone"],
                                  "relationship": g["relationship"], "is_primary": bool(g["is_primary"]),
                                  "telegram_vinculado": g["telegram_vinculado"], "tem_foto": g["tem_foto"]}
                                 for g in gs]
        else:
            # Privacidade: perfis sem acesso a contatos não recebem nome/celular do responsável.
            s["guardian_name"] = ""
            s["guardian_phone"] = ""
            s["guardian_hidden"] = True
            s["responsaveis"] = []
    return students


@router.post("")
def register_student(
    request: Request,
    registration_number: str = Form(...),
    name: str = Form(...),
    class_name: str = Form(""),
    telegram_chat_id: str = Form(""),
    guardian_name: str = Form(""),
    guardian_phone: str = Form(""),
    use_camera: str = Form(""),
    consent: str = Form(""),
    consent_guardian: str = Form(""),
    save_photo: str = Form(""),
    guardians: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    user=Depends(require("students.edit")),
):
    rm, name = registration_number.strip(), name.strip()
    extra = _parse_guardians(guardians) if has(user, "guardians.view") else []
    if not rm or not name:
        raise HTTPException(status_code=400, detail="RM e nome são obrigatórios.")
    rm = _rm(rm)
    if not _consent_given(consent):
        # LGPD art. 14: biometria de criança/adolescente só com consentimento do responsável.
        raise HTTPException(status_code=400, detail="Registre a autorização do responsável (termo de consentimento) "
                                                    "antes de cadastrar o rosto.")
    class_name = _class_name(class_name)
    chat_id = _clean_chat_id(telegram_chat_id) if has(user, "guardians.telegram") else ""
    phone = _clean_phone(guardian_phone) if has(user, "guardians.view") else ""
    if not has(user, "guardians.view"):
        guardian_name = ""

    if repository.get_student_by_registration(rm):
        raise HTTPException(
            status_code=409,
            detail=f"{rm} já cadastrado. Para melhorar o reconhecimento, use 'Adicionar foto' na aba Alunos.",
        )

    images = []
    embedding = _embedding_from_request(photo, use_camera, images) if _consent_given(save_photo) else _embedding_from_request(photo, use_camera)

    student_id = repository.create_student(
        name=name, registration_number=rm, telegram_chat_id=chat_id, class_name=class_name,
        guardian_name=guardian_name.strip(), guardian_phone=phone)
    if student_id is None:
        raise HTTPException(status_code=500, detail="Falha ao criar aluno no banco de dados.")
    # Responsáveis da tela nova (vários, com parentesco). O 1º é o principal.
    try:
        for i, g in enumerate(extra):
            gid = g["existing_id"] or gdb.create(g["name"], g["phone"])
            gdb.link(student_id, gid, g["relationship"], primary=(i == 0 and not guardian_name.strip()))
    except gdb.GuardianError as e:
        repository.delete_student(student_id)
        raise HTTPException(status_code=400, detail=str(e))
    first = extra[0]["name"] if extra else ""
    repository.set_consent(student_id, consent_guardian or guardian_name or first,
                           user.get("full_name") or user["username"],
                           int(school.get_settings().get("consent_version") or 1))
    if repository.add_student_face(student_id, embedding) is None:
        repository.delete_student(student_id)  # não deixa aluno sem rosto pela metade
        raise HTTPException(status_code=500, detail="Falha ao salvar o rosto do aluno.")

    if images:
        _save_primary_photo(student_id, images[0])
    engine.reload_faces()
    audit(request, user, "aluno_cadastrado", "aluno", student_id, f"{rm} · consentimento registrado")
    return {"status": "success", "id": student_id, "message": f"{name} cadastrado(a) com sucesso."}


@router.put("/{student_id}")
def update_student(
    student_id: int,
    request: Request,
    name: str = Form(...),
    registration_number: str = Form(...),
    class_name: str = Form(""),
    telegram_chat_id: Optional[str] = Form(None),
    guardian_name: str = Form(""),
    guardian_phone: str = Form(""),
    user=Depends(require("students.edit")),
):
    current = _get_or_404(student_id)
    rm, name = registration_number.strip(), name.strip()
    if not rm or not name:
        raise HTTPException(status_code=400, detail="RM e nome são obrigatórios.")
    if rm != (current.get("registration_number") or ""):
        rm = _rm(rm)     # RM antigo fora do padrão continua aceito enquanto não for trocado
    # Chat ID não enviado = mantém o atual (ele normalmente é preenchido pelo próprio bot).
    if telegram_chat_id is None or not has(user, "guardians.telegram"):
        chat_id = current.get("telegram_chat_id", "")
    else:
        chat_id = _clean_chat_id(telegram_chat_id)
    if has(user, "guardians.view"):
        phone = _clean_phone(guardian_phone)
    else:
        # Quem não vê os contatos também não os apaga sem querer ao salvar.
        guardian_name, phone = current.get("guardian_name", ""), current.get("guardian_phone", "")

    other = repository.get_student_by_registration(rm)
    if other and other["id"] != student_id:
        raise HTTPException(status_code=409, detail=f"{rm} já pertence a outro aluno.")

    class_name = _class_name(class_name)
    if not repository.update_student(student_id, name, rm, class_name, chat_id,
                                     guardian_name.strip(), phone):
        raise HTTPException(status_code=500, detail="Falha ao atualizar aluno.")
    engine.reload_faces()  # nomes/Chat IDs do cache do motor
    changed = [label for key, label, new in (
        ("name", "nome", name), ("registration_number", "RM", rm), ("class_name", "turma", class_name),
        ("guardian_name", "responsável", guardian_name.strip()), ("guardian_phone", "celular", phone),
        ("telegram_chat_id", "chat_id", chat_id)) if (current.get(key) or "") != (new or "")]
    audit(request, user, "aluno_alterado", "aluno", student_id, ", ".join(changed) or "sem mudanças")
    return {"status": "success"}


@router.delete("/{student_id}")
def delete_student(student_id: int, request: Request, user=Depends(require("students.delete"))):
    student = _get_or_404(student_id)
    if not repository.delete_student(student_id):
        raise HTTPException(status_code=500, detail="Falha ao excluir aluno.")
    from database import student_photos
    student_photos.remove(student_id)
    engine.reload_faces()
    audit(request, user, "aluno_excluido", "aluno", student_id, display_rm(student.get('registration_number', '')))
    return {"status": "success"}


@router.post("/{student_id}/faces")
def add_face(
    student_id: int,
    request: Request,
    use_camera: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    save_photo: str = Form(""),
    user=Depends(require("students.edit")),
):
    student = _get_or_404(student_id)
    if not student.get("consent_at"):
        raise HTTPException(status_code=400, detail="Registre o consentimento do responsável antes de adicionar fotos.")
    images = []
    embedding = _embedding_from_request(photo, use_camera, images) if _consent_given(save_photo) else _embedding_from_request(photo, use_camera)
    if repository.add_student_face(student_id, embedding) is None:
        raise HTTPException(status_code=500, detail="Falha ao salvar o rosto.")
    if images:
        _save_primary_photo(student_id, images[0])
    engine.reload_faces()
    audit(request, user, "aluno_foto_adicionada", "aluno", student_id,
          "câmera do sistema" if str(use_camera).strip().lower() in ("1", "true", "on", "yes") else "arquivo/webcam")
    return {"status": "success", "message": f"Foto adicionada para {student['name']}."}


@router.post("/{student_id}/test-telegram")
def test_telegram(student_id: int, request: Request, user=Depends(require("guardians.telegram"))):
    """Envia agora uma mensagem de teste — confere o Chat ID sem esperar passar na câmera."""
    student = _get_or_404(student_id)
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram.")
    chats = _chats(student)
    if not chats:
        raise HTTPException(status_code=400, detail="Nenhum responsável deste aluno vinculou o Telegram.")
    text = test_message(presence_message(
        student, engine.settings.camera_name, "ENTRY", _deadline(student)))
    sent = [engine.notifier.send_message(c, text, "HTML") for c in chats]
    ok = any(r[0] for r in sent)
    detail = next((r[1] for r in sent if not r[0]), "")
    if not ok:
        raise HTTPException(
            status_code=502,
            detail=f"{detail} (a pessoa precisa ter enviado /start para o bot antes).",
        )
    audit(request, user, "telegram_teste", "aluno", student_id)
    return {"status": "success", "message": "Mensagem de teste enviada."}


@router.post("/{student_id}/notify-absence")
def notify_absence(student_id: int, request: Request, user=Depends(require("absences.notify"))):
    """Portaria: avisa o responsável no Telegram que o aluno não registrou entrada hoje."""
    student = _get_or_404(student_id)
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram.")
    if not engine.guardians_enabled:
        raise HTTPException(status_code=400, detail="Os avisos aos responsáveis estão desligados em Escola → Integrações → Telegram.")
    chats = _chats(student, "ausencia")
    if not chats:
        raise HTTPException(status_code=400, detail="Nenhum responsável deste aluno vinculou o Telegram "
                                                    "(ou todos desligaram o aviso de ausência no bot).")
    n_ok, err = _send_now(student, chats, absence_message(student, _deadline(student)), "ABSENCE", user)
    if not n_ok:
        raise HTTPException(status_code=502, detail=err)
    audit(request, user, "ausencia_avisada", "aluno", student_id, f"{n_ok} responsável(is)")
    return {"status": "success", "message": "Responsável notificado." if n_ok == 1 else f"{n_ok} responsáveis notificados."}


def _who_sent(user: dict) -> str:
    return user.get("name") or user.get("username") or ""


def _send_now(student: dict, chats: list, text: str, kind: str, user: dict):
    """Envia na hora para cada responsável e registra no histórico. Devolve (quantos receberam, 1º erro)."""
    n_ok, err = 0, ""
    for c in chats:
        ok, info = engine.notifier.send_message(c, text, "HTML")
        nlog.log(student["id"], student.get("name", ""), kind, chat_id=c, status=nlog.SENT if ok else nlog.FAILED,
                 detail=info if not ok else "", sent_by=_who_sent(user))
        n_ok += 1 if ok else 0
        err = err or ("" if ok else info)
    return n_ok, err


def _enqueue_logged(student: dict, chats: list, text: str, kind: str, user: dict) -> None:
    """Põe na fila (envio em lote) e o histórico acompanha a resposta do Telegram."""
    for c in chats:
        lid = nlog.log(student["id"], student.get("name", ""), kind, chat_id=c, sent_by=_who_sent(user))
        engine.notifier.enqueue(c, text, lambda ok, info, lid=lid: nlog.update(lid, nlog.SENT if ok else nlog.FAILED,
                                                                              "" if ok else info), parse_mode="HTML")


def _guardians_ready() -> None:
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram.")
    if not engine.guardians_enabled:
        raise HTTPException(status_code=400, detail="Os avisos aos responsáveis estão desligados em Escola → Integrações → Telegram.")


def _early_exit_of(student_id: int):
    from database.reports import get_overview
    return next((e for e in get_overview(engine.settings.entry_tolerance).get("saidas_antecipadas_lista", [])
                 if e["id"] == student_id), None)


@router.post("/{student_id}/notify-early-exit")
def notify_early_exit(student_id: int, request: Request, user=Depends(require("absences.notify"))):
    """Portaria: avisa os responsáveis que o aluno saiu antes do fim do turno hoje."""
    student = _get_or_404(student_id)
    _guardians_ready()
    early = _early_exit_of(student_id)
    if not early:
        raise HTTPException(status_code=400, detail="Este aluno não tem saída antecipada registrada hoje.")
    chats = _chats(student, "saida")
    if not chats:
        raise HTTPException(status_code=400, detail="Nenhum responsável deste aluno vinculou o Telegram "
                                                    "(ou todos desligaram o aviso de saída no bot).")
    n_ok, err = _send_now(student, chats, early_exit_message(student, early["saida"], early["fim_turno"]), "EARLY_EXIT", user)
    if not n_ok:
        raise HTTPException(status_code=502, detail=err)
    audit(request, user, "saida_antecipada_avisada", "aluno", student_id, f"{n_ok} responsável(is)")
    return {"status": "success", "message": "Responsável notificado." if n_ok == 1 else f"{n_ok} responsáveis notificados."}


@router.post("/notify-early-exits")
def notify_early_exits(request: Request, user=Depends(require("absences.notify"))):
    """Avisa de uma vez os responsáveis de todas as saídas antecipadas do dia."""
    from database.reports import get_overview
    _guardians_ready()
    sent = skipped = 0
    for e in get_overview(engine.settings.entry_tolerance).get("saidas_antecipadas_lista", []):
        student = repository.get_student(e["id"])
        chats = _chats(student, "saida") if student else []
        if not chats:
            skipped += 1
            continue
        _enqueue_logged(student, chats, early_exit_message(student, e["saida"], e["fim_turno"]), "EARLY_EXIT", user)
        sent += 1
    audit(request, user, "saidas_antecipadas_avisadas", "aluno", "", f"{sent} avisado(s), {skipped} sem Telegram")
    msg = f"{sent} responsável(is) avisado(s)." if sent else "Nenhuma saída antecipada para avisar."
    if skipped:
        msg += f" {skipped} sem Telegram vinculado."
    return {"status": "success", "enviados": sent, "sem_telegram": skipped, "message": msg}


def _whatsapp_number(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if 10 <= len(digits) <= 11:
        digits = "55" + digits      # Brasil por padrão
    return digits


@router.get("/{student_id}/passagens")
def student_passages(student_id: int, limit: int = 15, user=Depends(require("students.view"))):
    """
    Passagens na câmera (o fato bruto), em qualquer dia — inclusive feriado, fim de semana ou fora
    do ano letivo, quando a frequência não conta. É o que mostra "a câmera viu o aluno agora".
    """
    from app.arena import enabled, arena
    if enabled():
        import json
        from database.connection import get_db_connection
        with get_db_connection() as conn:
            rows=conn.execute('SELECT payload FROM arena_events WHERE session=? ORDER BY id DESC', (arena.session,)).fetchall()
        return [dict(e, direction=e['kind'], camera=str(e.get('camera_id',''))) for e in (json.loads(r[0]) for r in rows) if e.get('student_id')==student_id][:max(1,min(100,limit))]
    from database.connection import get_db_connection
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT p.id, p.direction, p.created_at, p.telegram_status, p.telegram_detail, p.similarity, "
            "c.name AS camera FROM presences p LEFT JOIN cameras c ON c.id = p.camera_id "
            "WHERE p.student_id = ? ORDER BY p.id DESC LIMIT ?", (int(student_id), max(1, min(int(limit), 100))))]
    for r in rows:
        r["telegram_detail"] = nlog.detail_pt(r.get("telegram_detail") or "")
    return rows


@router.get("/{student_id}/telegram-invite")
def telegram_invite(student_id: int, request: Request, user=Depends(require("guardians.telegram"))):
    """
    Convite para o responsável: link do bot com código único do aluno.
    Ao abrir e tocar em Iniciar, o Telegram é vinculado sozinho — ninguém
    precisa descobrir Chat ID. Também devolve o link pronto do WhatsApp.
    """
    student = _get_or_404(student_id)
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram.")
    bot = engine.notifier.get_bot_username()
    if not bot:
        raise HTTPException(status_code=503, detail="Não consegui falar com o Telegram agora (sem internet?).")
    token = repository.ensure_link_token(student_id)
    link = f"https://t.me/{bot}?start={token}"
    guardian = (student.get("guardian_name") or "").split(" ")[0] or "tudo bem"
    text = (f"Olá, {guardian}! Para receber no Telegram os avisos de entrada e saída de "
            f"{student['name']} na escola, abra o link e toque em Iniciar: {link}")
    number = _whatsapp_number(student.get("guardian_phone", ""))
    whatsapp = f"https://wa.me/{number}?text={quote(text)}" if number else f"https://wa.me/?text={quote(text)}"
    audit(request, user, "telegram_convite", "aluno", student_id)
    return {"bot": bot, "link": link, "mensagem": text, "whatsapp": whatsapp,
            "vinculado": bool(student.get("telegram_chat_id"))}


@router.post("/{student_id}/telegram-unlink")
def telegram_unlink(student_id: int, request: Request, user=Depends(require("guardians.telegram"))):
    _get_or_404(student_id)
    repository.unlink_telegram(student_id)
    engine.refresh_students()
    audit(request, user, "telegram_desvinculado", "aluno", student_id)
    return {"status": "success", "message": "Telegram desvinculado."}


@router.post("/notify-absences")
def notify_absences(request: Request, user=Depends(require("absences.notify"))):
    """Avisa de uma vez os responsáveis de todos os ausentes do dia (quem tem Telegram vinculado)."""
    from database.reports import get_overview
    if not engine.notifier.enabled:
        raise HTTPException(status_code=400, detail="O bot do Telegram não está configurado. Configure em Escola → Integrações → Telegram.")
    if not engine.guardians_enabled:
        raise HTTPException(status_code=400, detail="Os avisos aos responsáveis estão desligados em Escola → Integrações → Telegram.")
    overview = get_overview(engine.settings.entry_tolerance)
    if not overview["passou_tolerancia"]:
        raise HTTPException(status_code=400, detail=f"Ausências só contam depois de {overview['tolerancia']}.")
    if not overview.get("ausentes"):
        return {"status": "success", "enviados": 0, "sem_telegram": 0, "message": "Nenhum ausente no momento."}
    sent = skipped = 0
    for a in overview["ausentes"]:
        student = repository.get_student(a["id"])
        chats = _chats(student, "ausencia") if student else []
        if not chats:
            skipped += 1
            continue
        _enqueue_logged(student, chats, absence_message(student, _deadline(student)), "ABSENCE", user)
        sent += 1
    audit(request, user, "ausencias_avisadas", "aluno", "", f"{sent} avisado(s), {skipped} sem Telegram")
    msg = f"{sent} responsável(is) avisado(s)."
    if skipped:
        msg += f" {skipped} sem Telegram vinculado."
    return {"status": "success", "enviados": sent, "sem_telegram": skipped, "message": msg}


# =====================================================================
# Consentimento da biometria (Fase 1)
# =====================================================================

@router.post("/{student_id}/consent")
def register_consent(student_id: int, request: Request, guardian: str = Form(""),
                     user=Depends(require("students.edit"))):
    """Registra que o responsável assinou o termo (alunos cadastrados antes da Fase 1)."""
    student = _get_or_404(student_id)
    version = int(school.get_settings().get("consent_version") or 1)
    repository.set_consent(student_id, guardian or student.get("guardian_name") or "",
                           user.get("full_name") or user["username"], version)
    audit(request, user, "consentimento_registrado", "aluno", student_id, f"termo versão {version}")
    return {"status": "success", "message": "Consentimento registrado."}


@router.delete("/{student_id}/consent")
def revoke_consent(student_id: int, request: Request, user=Depends(require("students.edit"))):
    """Revogação pelo responsável: apaga o consentimento e TODOS os vetores faciais do aluno."""
    _get_or_404(student_id)
    repository.revoke_consent(student_id)
    from database import student_photos
    student_photos.remove(student_id)
    engine.reload_faces()
    audit(request, user, "consentimento_revogado", "aluno", student_id, "vetores faciais apagados")
    return {"status": "success", "message": "Consentimento revogado e biometria apagada. "
                                            "A frequência deste aluno passa a ser manual."}
