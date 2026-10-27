"""
Assistente virtual (1ª camada de suporte) — ver app/assistant/service.py.

* assistant.use    : conversar e pedir o especialista (todos os perfis padrão).
* assistant.manage : chave do Gemini, modelo e Telegram do suporte (Administrador).
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.security import audit, require
from app.assistant import service as ast
from app.engine import engine
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistente", tags=["assistente"])


class Message(BaseModel):
    papel: str
    texto: str


class ChatPayload(BaseModel):
    mensagens: List[Message]
    tela: Optional[str] = ""


class EscalatePayload(BaseModel):
    mensagens: List[Message] = []
    tela: Optional[str] = ""
    observacao: Optional[str] = ""


class FeedbackPayload(BaseModel):
    util: bool
    pergunta: Optional[str] = ""
    resposta: Optional[str] = ""
    tela: Optional[str] = ""


class ConfigPayload(BaseModel):
    ativo: Optional[bool] = None
    modelo: Optional[str] = None
    chat_suporte: Optional[str] = None


class KeyPayload(BaseModel):
    chave: str


class TestKeyPayload(BaseModel):
    chave: Optional[str] = None
    modelo: Optional[str] = None


class TokenPayload(BaseModel):
    token: str


def _fail(e: ast.AssistantError):
    raise HTTPException(status_code=e.status, detail=str(e))


def _msgs(items: List[Message]) -> List[Dict[str, Any]]:
    return [{"papel": m.papel, "texto": m.texto} for m in items or []]


def _by(user: Dict[str, Any]) -> str:
    return user.get("username", "")


def _my_telegram(user_id: Any) -> str:
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT telegram_user_id FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return (row[0] if row else "") or ""
    except Exception:
        return ""


# ================================================================== uso (todos os perfis)
@router.get("/estado")
def state(user=Depends(require("assistant.use"))):
    """O que o widget precisa saber: se a IA responde, se dá para escalar e as perguntas frequentes."""
    key = ast.gemini_key()[0]
    notifier = ast.support_notifier(engine)
    return {
        "disponivel": bool(key) and ast.is_enabled(),
        "ativo": ast.is_enabled(),
        "escalar": bool(ast.support_chat()[0]) and bool(notifier and notifier.enabled),
        "faq": ast.faq(),
    }


@router.post("/chat")
def chat(payload: ChatPayload, user=Depends(require("assistant.use"))):
    wait = ast.chat_limiter.hit(str(user.get("id")))
    if wait:
        raise HTTPException(status_code=429, detail=f"Muitas perguntas seguidas. Aguarde {wait} s.")
    try:
        return ast.ask(_msgs(payload.mensagens), payload.tela or "", user.get("role_name") or user.get("role") or "")
    except ast.AssistantError as e:
        _fail(e)


@router.post("/escalar")
def escalate(payload: EscalatePayload, request: Request, user=Depends(require("assistant.use"))):
    wait = ast.escalation_limiter.hit(str(user.get("id")))
    if wait:
        raise HTTPException(status_code=429, detail=f"Você já enviou pedidos há pouco. Aguarde {max(1, wait // 60)} min.")
    try:
        protocol = ast.escalate(engine, user, _msgs(payload.mensagens), payload.tela or "", payload.observacao or "")
    except ast.AssistantError as e:
        _fail(e)
    audit(request, user, "assistente_escalado", "assistente", protocol, (payload.tela or "")[:80])
    return {"protocolo": protocol,
            "message": f"Pedido enviado ao especialista. Protocolo {protocol}. A equipe de suporte vai falar com você."}


@router.post("/avaliacao")
def feedback(payload: FeedbackPayload, request: Request, user=Depends(require("assistant.use"))):
    """👍/👎 numa resposta. Fica na auditoria (pergunta mascarada) para revisar a base depois."""
    wait = ast.chat_limiter.hit(f"fb:{user.get('id')}")
    if wait:
        raise HTTPException(status_code=429, detail="Aguarde um instante.")
    question = ast.mask_personal(" ".join((payload.pergunta or "").split()))[:160]
    detail = ("útil" if payload.util else "não ajudou") + (f" · {question}" if question else "")
    audit(request, user, "assistente_avaliado", "assistente", "", detail)
    return {"ok": True}


# ================================================================== configuração (Administrador)
def _config(user: Dict[str, Any]) -> Dict[str, Any]:
    key, key_origin = ast.gemini_key()
    chat, chat_origin = ast.support_chat()
    bot_token, bot_origin = ast.support_bot_token()
    notifier = ast.support_notifier(engine)
    if bot_token:
        via = "proprio"
    elif getattr(engine, "security_separate", False):
        via = "seguranca"
    else:
        via = "responsaveis"
    mine = _my_telegram(user.get("id"))
    return {
        "ativo": ast.is_enabled(),
        "chave_mascarada": ast.mask_key(key),
        "chave_origem": key_origin,
        "chave_atualizada": ast.ss.get_meta(ast.KEY) if key_origin == "tela" else {},
        "modelo": ast.model_name(),
        "modelo_padrao": ast.DEFAULT_MODEL,
        "chat_suporte": chat,
        "chat_origem": chat_origin,
        "bot_proprio_mascarado": ast.ss.mask_token(bot_token),
        "bot_origem": bot_origin,
        "bot_via": via,
        "bot_configurado": bool(notifier and notifier.enabled),
        "bot_nome": notifier.get_bot_username(block=False) if notifier and notifier.enabled else None,
        "bot_dedicado": ast.support_bot_is_dedicated(engine),
        "meu_telegram": mine,
        "base_tamanho": len(ast.knowledge()),
    }


@router.get("/config")
def get_config(user=Depends(require("assistant.manage"))):
    return _config(user)


@router.put("/config")
def save_config(payload: ConfigPayload, request: Request, user=Depends(require("assistant.manage"))):
    changed = []
    try:
        if payload.ativo is not None:
            ast.save(ast.ENABLED, "1" if payload.ativo else "0", _by(user))
            changed.append("ligado" if payload.ativo else "desligado")
        if payload.modelo is not None:
            model = ast.clean_model(payload.modelo)
            ast.save(ast.MODEL, model or None, _by(user))
            changed.append(f"modelo {model or ast.DEFAULT_MODEL}")
        if payload.chat_suporte is not None:
            chat = ast.clean_chat(payload.chat_suporte)
            ast.save(ast.SUPPORT_CHAT, chat or None, _by(user))
            changed.append("Telegram do suporte " + ("alterado" if chat else "removido"))
    except ast.AssistantError as e:
        _fail(e)
    audit(request, user, "assistente_configurado", "assistente", "", ", ".join(changed))
    return dict(_config(user), message="Configuração do assistente salva.")


@router.put("/config/chave")
def save_key(payload: KeyPayload, request: Request, user=Depends(require("assistant.manage"))):
    try:
        key = ast.clean_key(payload.chave)
    except ast.AssistantError as e:
        _fail(e)
    warning = ""
    try:
        ast.test_key(key=key)
    except ast.AssistantError as e:
        if e.status == 503 and "conexão" in str(e).lower():
            warning = "Sem internet para conferir agora; a chave foi salva e será usada quando a conexão voltar."
        else:
            _fail(e)
    ast.save(ast.KEY, key, _by(user))
    audit(request, user, "assistente_chave_alterada", "assistente", "", "salva sem conferir (sem rede)" if warning else "")
    return dict(_config(user), message=warning or "Chave salva. O Gemini respondeu: o assistente está pronto.")


@router.delete("/config/chave")
def remove_key(request: Request, user=Depends(require("assistant.manage"))):
    ast.save(ast.KEY, None)
    audit(request, user, "assistente_chave_removida", "assistente")
    left = ast.gemini_key()[0]
    return dict(_config(user), message="Chave removida da tela. Vale a do realguard.env." if left
                else "Chave removida. O assistente passa a mostrar só as perguntas frequentes.")


@router.post("/config/testar-ia")
def test_ai(payload: TestKeyPayload, user=Depends(require("assistant.manage"))):
    """Testa a chave digitada (sem salvar) ou a atual, com o modelo digitado ou o atual."""
    try:
        key = ast.clean_key(payload.chave) if (payload.chave or "").strip() else None
        model = ast.clean_model(payload.modelo or "") or None
        answer = ast.test_key(key=key, model=model)
    except ast.AssistantError as e:
        _fail(e)
    return {"ok": True, "message": f"O Gemini respondeu ({model or ast.model_name()}): {answer[:60]}"}


@router.put("/config/bot")
def save_bot(payload: TokenPayload, request: Request, user=Depends(require("assistant.manage"))):
    try:
        token = ast.ss.clean_token(payload.token)
    except ast.ss.SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ok, info = engine.notifier.check_token(token)
    if not ok and not info.startswith("Falha de rede"):
        raise HTTPException(status_code=400, detail=f"O Telegram recusou este token: {info}")
    ast.save(ast.SUPPORT_BOT, token, _by(user))
    audit(request, user, "assistente_bot_alterado", "assistente", "", f"bot @{info}" if ok else "salvo sem conferir")
    msg = (f"Bot do suporte salvo (@{info}). Abra esse bot no seu Telegram e toque em Iniciar." if ok
           else "Sem internet para conferir agora; o token foi salvo.")
    return dict(_config(user), message=msg)


@router.delete("/config/bot")
def remove_bot(request: Request, user=Depends(require("assistant.manage"))):
    ast.save(ast.SUPPORT_BOT, None)
    audit(request, user, "assistente_bot_removido", "assistente")
    return dict(_config(user), message="Bot próprio removido: os pedidos saem pelo bot da segurança "
                                       "(ou pelo dos responsáveis).")


@router.post("/config/usar-meu-telegram")
def use_my_telegram(request: Request, user=Depends(require("assistant.manage"))):
    mine = _my_telegram(user.get("id"))
    if not mine:
        raise HTTPException(status_code=400, detail="Sua conta ainda não está ligada ao Telegram. Clique no seu nome "
                                                    "(canto inferior esquerdo) → Vincular Telegram e tente de novo.")
    ast.save(ast.SUPPORT_CHAT, mine, _by(user))
    audit(request, user, "assistente_configurado", "assistente", "", "Telegram do suporte = conta de quem configurou")
    return dict(_config(user), message="Pronto: os pedidos de suporte vão para o seu Telegram.")


@router.get("/config/chats")
def recent_chats(user=Depends(require("assistant.manage"))):
    """Quem escreveu para o bot PRÓPRIO do suporte (para descobrir o Chat ID). Só com bot dedicado."""
    if not ast.support_bot_is_dedicated(engine):
        raise HTTPException(status_code=400, detail="Disponível só com um bot próprio do suporte (que não seja o dos "
                                                    "responsáveis nem o da segurança).")
    ok, result = ast.support_notifier(engine).recent_chats()
    if not ok:
        raise HTTPException(status_code=400, detail=f"Não consegui consultar o bot: {result}")
    return {"chats": result}


@router.post("/config/testar-suporte")
def test_support(request: Request, user=Depends(require("assistant.manage"))):
    try:
        ast.send_support_test(engine, user.get("full_name") or _by(user))
    except ast.AssistantError as e:
        _fail(e)
    audit(request, user, "assistente_teste_suporte", "assistente")
    return {"ok": True, "message": "Teste enviado. Confira o Telegram do suporte."}
