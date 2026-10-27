"""Integrações configuráveis pela tela (hoje: Telegram). Permissão telegram.manage (admin e gestor)."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.security import audit, require
from app.engine import engine
from database import access, repository, system_settings as ss
from database.guardians import count_linked as gdb_count
from app.security_alerts import panel_enabled, reminder_minutes, request_panel_update

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integracoes", tags=["integracoes"])


class TokenPayload(BaseModel):
    token: str


class TestPayload(BaseModel):
    token: Optional[str] = None


class TelegramPayload(BaseModel):
    avisos_responsaveis: bool = True
    seguranca: Optional[List[str]] = None     # None = mantém a lista atual (convites entram sozinhos)
    modelo: Optional[str] = None
    lembrete_min: Optional[int] = None    # alerta sem decisão: lembrete após N min (0 = desligado)
    painel_fixo: Optional[bool] = None    # mensagem fixada com a lista de pendências


def _known_chats() -> dict:
    return {c["chat_id"]: c for c in repository.list_telegram_chats(200)}


BOTS = ("responsaveis", "seguranca")


def _bot_kind(bot: str) -> str:
    if bot not in BOTS:
        raise HTTPException(status_code=400, detail="Bot inválido: use responsaveis ou seguranca.")
    return bot


def _bot_state(kind: str) -> dict:
    cfg = engine.telegram
    sec = kind == "seguranca"
    notifier = engine.sec_notifier if sec else engine.notifier
    origin = cfg.get("sec_token_origem" if sec else "token_origem", "")
    key = ss.TG_SEC_TOKEN if sec else ss.TG_TOKEN
    token = notifier.token
    return {
        "configurado": notifier.enabled,
        "token_mascarado": ss.mask_token(token),
        "token_origem": origin,                              # tela | env | ""
        "token_atualizado": ss.get_meta(key) if origin == "tela" else {},
        "bot": notifier.get_bot_username(block=False) if token else None,
        "erro": (engine.sec_linker if sec else engine.linker).last_error if notifier.enabled else None,
    }


def _state() -> dict:
    cfg = engine.telegram
    resp, sec = _bot_state("responsaveis"), _bot_state("seguranca")
    return dict(resp, **{
        "bots": {"responsaveis": resp, "seguranca": sec},
        # sem bot próprio da segurança, os alertas saem pelo bot dos responsáveis
        "seguranca_separado": engine.security_separate,
        "avisos_responsaveis": cfg.get("avisos_responsaveis", True),
        # Alertas de segurança: privado de cada conta vinculada (sem grupos nem lista manual)
        "destinatarios": access.security_people(),
        "modelo": cfg.get("modelo", ""),
        "campos_modelo": ["{" + f + "}" for f in ss.TEMPLATE_FIELDS],
        "vinculados": gdb_count(),
        "lembrete_min": reminder_minutes(),
        "painel_fixo": panel_enabled(),
    })


@router.get("/telegram")
def get_telegram(user=Depends(require("telegram.manage"))):
    return _state()


@router.post("/telegram/testar")
def test_token(payload: TestPayload, bot: str = "responsaveis", user=Depends(require("telegram.manage"))):
    """Testa o token digitado (sem salvar) ou o atual do bot escolhido."""
    notifier = engine.sec_notifier if _bot_kind(bot) == "seguranca" else engine.notifier
    token = (payload.token or "").strip()
    if token:
        try:
            token = ss.clean_token(token)
        except ss.SettingsError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        token = notifier.token
        if not token:
            raise HTTPException(status_code=400, detail="Nenhum token configurado ainda.")
    ok, info = notifier.check_token(token)
    if not ok:
        raise HTTPException(status_code=400, detail=f"O Telegram recusou: {info}")
    return {"ok": True, "bot": info, "message": f"Conectado ao bot @{info}."}


@router.put("/telegram/token")
def save_token(payload: TokenPayload, request: Request, bot: str = "responsaveis",
               user=Depends(require("telegram.manage"))):
    sec = _bot_kind(bot) == "seguranca"
    try:
        token = ss.clean_token(payload.token)
    except ss.SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    other = engine.notifier.token if sec else engine.sec_notifier.token
    if other and token == other:
        raise HTTPException(status_code=400, detail="Este token já é o do outro bot. Crie um bot separado no "
                                                    "@BotFather (ou deixe o da segurança vazio para usar o mesmo).")
    ok, info = engine.notifier.check_token(token)
    warning = ""
    if not ok:
        if info.startswith("Falha de rede"):
            warning = "Sem internet para conferir agora; o token foi salvo e será usado quando a conexão voltar."
        else:
            raise HTTPException(status_code=400, detail=f"O Telegram recusou este token: {info}")
    ss.set_value(ss.TG_SEC_TOKEN if sec else ss.TG_TOKEN, token, user.get("username", ""))
    engine.apply_telegram()
    audit(request, user, "telegram_token_alterado", "integracao", "telegram_" + bot,
          f"bot @{info}" if ok else "salvo sem conferir (sem rede)")
    extra = (" Quem é da segurança precisa vincular a conta de novo, agora neste bot (nome → Vincular Telegram)."
             if sec else "")
    return dict(_state(), message=(warning or f"Token salvo. Bot @{info} conectado.") + extra)


@router.delete("/telegram/token")
def remove_token(request: Request, bot: str = "responsaveis", user=Depends(require("telegram.manage"))):
    sec = _bot_kind(bot) == "seguranca"
    ss.set_value(ss.TG_SEC_TOKEN if sec else ss.TG_TOKEN, None)
    engine.apply_telegram()
    audit(request, user, "telegram_token_removido", "integracao", "telegram_" + bot)
    notifier = engine.sec_notifier if sec else engine.notifier
    if sec:
        msg = ("Token removido da tela. Vale o do arquivo realguard.env." if notifier.enabled
               else "Token da segurança removido: os alertas passam a sair pelo bot dos responsáveis.")
    else:
        msg = ("Token removido da tela. Vale o do arquivo realguard.env." if notifier.enabled
               else "Token removido. Nenhuma mensagem será enviada até configurar outro.")
    return dict(_state(), message=msg)


@router.put("/telegram")
def save_telegram(payload: TelegramPayload, request: Request, user=Depends(require("telegram.manage"))):
    try:
        ids = ss.clean_chat_ids(payload.seguranca) if payload.seguranca is not None \
            else list(engine.telegram.get("seguranca") or [])
        template = ss.clean_template(payload.modelo) if payload.modelo is not None else (engine.telegram.get("modelo") or "")
    except ss.SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    before = dict(engine.telegram)
    by = user.get("username", "")
    ss.set_value(ss.TG_GUARDIANS, "1" if payload.avisos_responsaveis else "0", by)
    ss.set_value(ss.TG_SECURITY, ",".join(ids), by)
    ss.set_value(ss.TG_TEMPLATE, template, by)
    if payload.painel_fixo is not None:
        ss.set_value("telegram_panel", "1" if payload.painel_fixo else "0", by)
    if payload.lembrete_min is not None:
        ss.set_value("telegram_reminder_min", str(max(0, min(240, int(payload.lembrete_min)))), by)
    engine.apply_telegram()
    changes = []
    if before.get("avisos_responsaveis") != payload.avisos_responsaveis:
        changes.append("avisos aos responsáveis " + ("ligados" if payload.avisos_responsaveis else "DESLIGADOS"))
    if list(before.get("seguranca") or []) != ids:
        changes.append(f"{len(ids)} destinatário(s) de segurança")
    if (before.get("modelo") or "") != template:
        changes.append("mensagem " + ("personalizada" if template else "padrão"))
    audit(request, user, "telegram_config", "integracao", "telegram", "; ".join(changes) or "sem mudanças")
    request_panel_update()
    return dict(_state(), message="Configuração do Telegram salva.")


@router.post("/telegram/seguranca/testar")
def test_security(request: Request, user=Depends(require("telegram.manage"))):
    """Mensagem de teste no privado de cada pessoa que recebe os alertas."""
    notifier = engine.security_notifier
    if not notifier.enabled:
        raise HTTPException(status_code=400, detail="Configure o token do bot primeiro.")
    people = access.alert_recipients()
    if not people:
        raise HTTPException(status_code=400, detail="Ninguém recebe os alertas ainda: cada pessoa precisa vincular "
                                                    "a conta (nome dela → Vincular Telegram).")
    results = []
    for p in people:
        ok, detail = notifier.send_message(
            p["chat"], "🔔 <b>RealGuard</b> — teste dos alertas de segurança. Se chegou aqui, está tudo certo.", "HTML")
        results.append({"nome": p["nome"], "ok": ok, "detalhe": detail})
    audit(request, user, "telegram_teste", "integracao", "seguranca",
          f"{sum(r['ok'] for r in results)}/{len(results)} entregues")
    failed = [r for r in results if not r["ok"]]
    return {"resultados": results,
            "message": f"Teste entregue a {len(results)} pessoa(s)." if not failed else
            f"Falhou para {', '.join(r['nome'] for r in failed)}: {failed[0]['detalhe']}"}


# ---------------------------------------------------------------- pessoas dos alertas (pelo administrador)
# Na feira (ou numa escola com um só computador), ninguém entra com o próprio login para
# fazer o "Vincular Telegram". O administrador cadastra a pessoa aqui e mostra um QR Code
# na tela: ela aponta a câmera do celular, toca em Iniciar no bot e passa a receber os
# alertas — com o nome dela na auditoria quando confirmar ou descartar.
class PersonPayload(BaseModel):
    nome: str
    perfil: str = "operador"


def _username_for(name: str) -> str:
    import re
    import unicodedata
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    parts = re.sub(r"[^a-z0-9 ]", "", base).split()
    slug = ".".join(parts[:1] + parts[-1:]) if len(parts) > 1 else (parts[0] if parts else "alerta")
    slug = (slug or "alerta")[:24]
    if len(slug) < 3:
        slug = (slug + "alerta")[:24]
    cand, n = slug, 2
    while access.get_user_by_username(cand):
        cand, n = f"{slug}{n}", n + 1
    return cand


def _qr_png(text: str) -> str:
    """QR Code em data URL (OpenCV, que o sistema já usa). Sem OpenCV com QR: só o link."""
    try:
        import base64
        import cv2
        qr = cv2.QRCodeEncoder_create().encode(text)
        img = cv2.resize(qr, (qr.shape[1] * 8, qr.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
        img = cv2.copyMakeBorder(img, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
        ok, buf = cv2.imencode(".png", img)
        return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""
    except Exception as e:
        logger.info(f"QR Code indisponível ({e}); a tela mostra só o link.")
        return ""


@router.get("/telegram/pessoas/perfis")
def person_roles(user=Depends(require("users.manage"))):
    """Perfis que podem receber alertas (precisam de 'Ver ocorrências')."""
    return [{"chave": r["key"], "nome": r["name"], "decide": "events.decide" in r["permissions"]}
            for r in access.list_roles() if "events.view" in r["permissions"]]


@router.post("/telegram/pessoas")
def add_person(payload: PersonPayload, request: Request, user=Depends(require("users.manage"))):
    import secrets
    name = " ".join((payload.nome or "").split())
    if len(name) < 3:
        raise HTTPException(status_code=400, detail="Informe o nome da pessoa.")
    role = access.get_role(payload.perfil)
    if not role or "events.view" not in role["permissions"]:
        raise HTTPException(status_code=400, detail="Escolha um perfil que possa ver as ocorrências.")
    try:
        # senha aleatória e provisória: a pessoa não precisa entrar no sistema (só usa o Telegram);
        # se um dia precisar, o administrador redefine a senha em Administração → Usuários.
        new = access.create_user(_username_for(name), name, payload.perfil, "Rg-" + secrets.token_urlsafe(18), True)
    except access.AccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit(request, user, "usuario_criado", "usuario", new["id"], f"{name} ({role['name']}) para receber alertas")
    return {"id": new["id"], "usuario": new["username"], "nome": name,
            "message": f"{name} cadastrado(a). Mostre o QR Code para vincular o Telegram."}


@router.post("/telegram/pessoas/{user_id}/convite")
def person_invite(user_id: int, request: Request, user=Depends(require("users.manage"))):
    """Link de uso único (15 min) + QR Code para a pessoa ligar o Telegram dela a esta conta."""
    target = access.get_user(user_id)
    if not target or not target.get("active"):
        raise HTTPException(status_code=404, detail="Usuário não encontrado ou desativado.")
    notifier = engine.security_notifier
    if not notifier.enabled:
        raise HTTPException(status_code=400, detail="Configure o bot da segurança (ou o dos responsáveis) primeiro.")
    bot = notifier.get_bot_username()
    if not bot:
        raise HTTPException(status_code=503, detail="Não consegui falar com o Telegram agora (sem internet?).")
    code = access.create_telegram_code(user_id)
    link = f"https://t.me/{bot}?start={code}"
    audit(request, user, "telegram_conta_link_gerado", "usuario", user_id, f"pelo administrador para {target['full_name']}")
    return {"link": link, "qr": _qr_png(link), "bot": bot, "expira_min": access.TELEGRAM_CODE_MINUTES,
            "nome": target["full_name"]}


@router.get("/telegram/pessoas/{user_id}")
def person_status(user_id: int, user=Depends(require("users.manage"))):
    return access.telegram_status(user_id)


@router.delete("/telegram/pessoas/{user_id}")
def person_unlink(user_id: int, request: Request, user=Depends(require("users.manage"))):
    """Tira a pessoa dos alertas: desfaz o vínculo do Telegram (a conta continua; desative em Usuários se quiser)."""
    old = access.unlink_telegram_user(user_id)
    if old:
        import threading
        threading.Thread(target=engine.security_bot.set_user_commands, args=(old, False), daemon=True).start()
    request_panel_update()
    audit(request, user, "telegram_conta_desvinculada", "usuario", user_id, "pelo administrador")
    return {"message": "Telegram desvinculado. A pessoa não recebe mais os alertas."}


# ---------------------------------------------------------------- mensagens aos responsáveis (uma por uma)
class MessagePayload(BaseModel):
    texto: str = ""


class PreviewPayload(BaseModel):
    tipo: str = "ENTRY"
    texto: Optional[str] = None


_SAMPLE_STUDENT = {"name": "Lucas Mendes", "class_name": "7º A", "registration_number": "1042"}
_SAMPLE_TIME = {"ENTRY": (7, 38), "LATE": (8, 12), "EXIT": (12, 25), "ABSENCE": (8, 45)}


def _sample(kind: str, text: Optional[str] = None) -> str:
    from datetime import datetime
    from app.notifications import templates as tpl
    h, m = _SAMPLE_TIME.get(kind, (7, 38))
    out = tpl.message(kind, _SAMPLE_STUDENT, datetime.now().replace(hour=h, minute=m), "Portão principal", "07:45", text)
    return out + ("\n🟠 <b>Antes do fim do turno</b> (aulas até 12:00)" if kind == "EARLY_EXIT" else "")


def _messages_state() -> dict:
    from app.notifications import templates as tpl
    saved = tpl.custom()
    return {
        "tipos": [{"tipo": k, "nome": n, "texto": tpl.text_for(k), "padrao": tpl.DEFAULTS[k],
                   "personalizada": k in saved, "previa": _sample(k)} for k, n in tpl.KINDS.items()],
        "variaveis": [{"chave": k, "nome": n} for k, n in tpl.VARIABLES.items()],
        "limite": tpl.MAX_LEN,
    }


@router.get("/telegram/mensagens")
def list_messages(user=Depends(require("telegram.manage"))):
    return _messages_state()


@router.post("/telegram/mensagens/previa")
def preview_message(payload: PreviewPayload, user=Depends(require("telegram.manage"))):
    """O texto exatamente como o responsável vai receber (sem salvar)."""
    from app.notifications import templates as tpl
    if payload.tipo not in tpl.KINDS:
        raise HTTPException(status_code=400, detail="Tipo de mensagem inválido.")
    try:
        text = tpl.validate(payload.texto) if payload.texto is not None else None
    except tpl.TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"html": _sample(payload.tipo, text or None)}


@router.put("/telegram/mensagens/{kind}")
def save_message(kind: str, payload: MessagePayload, request: Request, user=Depends(require("telegram.manage"))):
    from app.notifications import templates as tpl
    try:
        tpl.save(kind, payload.texto, user.get("username", ""))
    except tpl.TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # a mensagem antiga, única e sem formatação, sai de cena: valem as mensagens por tipo
    if engine.telegram.get("modelo"):
        ss.set_value(ss.TG_TEMPLATE, "", user.get("username", ""))
        engine.apply_telegram()
    custom = kind in tpl.custom()
    audit(request, user, "mensagem_responsavel", "integracao", kind,
          f"{tpl.KINDS.get(kind, kind)}: " + ("personalizada" if custom else "padrão"))
    return dict(_messages_state(), message=f"Mensagem “{tpl.KINDS[kind]}” " + ("salva." if custom else "voltou ao padrão."))
