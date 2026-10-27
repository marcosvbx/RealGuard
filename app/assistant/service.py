"""
Assistente virtual do RealGuard: 1ª camada de suporte dentro do painel.

* Responde dúvidas de OPERAÇÃO com base em app/assistant/realguard.md, usando a
  API do Gemini (Google). Ele não acessa banco, código nem dados da escola: só a
  base de conhecimento, a pergunta e o nome da tela aberta.
* Quando não sabe, ou quando a pessoa pede, escala para o especialista: a
  conversa vai, com um protocolo, para o Telegram configurado do suporte.
* Só biblioteca padrão (urllib), como o resto do sistema: nada novo para
  instalar na AIBOX (Python 3.8).

Configuração (o que for salvo pela tela tem prioridade sobre o realguard.env):
    GEMINI_API_KEY               chave criada no Google AI Studio
    GEMINI_MODEL                 modelo (padrão abaixo)
    ASSISTANT_SUPPORT_CHAT_ID    Chat ID do Telegram de quem recebe os pedidos de suporte
    ASSISTANT_SUPPORT_BOT_TOKEN  bot próprio do suporte (vazio = bot da segurança/responsáveis)
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from database import system_settings as ss

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "realguard.md")

# chaves em system_settings
KEY = "assistant_gemini_key"
MODEL = "assistant_model"
ENABLED = "assistant_enabled"
SUPPORT_CHAT = "assistant_support_chat"
SUPPORT_BOT = "assistant_support_bot_token"
PROTOCOL_SEQ = "assistant_protocol_seq"

MAX_HISTORY = 10          # mensagens enviadas ao modelo
MAX_MESSAGE_CHARS = 1500  # por mensagem
MAX_QUESTION_CHARS = 1000
TIMEOUT_SEC = 25.0
# Gemini sobrecarregado (500/502/503/504): tenta de novo sozinho, esperando estes segundos entre as tentativas.
RETRY_DELAYS = (1.0, 3.0)
TRANSIENT_CODES = (500, 502, 503, 504)
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,60}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{20,200}$")


class AssistantError(Exception):
    """Erro com mensagem pronta para a tela. status = código HTTP sugerido."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ================================================================== configuração
def _settings() -> Dict[str, str]:
    return ss.get_all()


def gemini_key() -> Tuple[str, str]:
    """(chave, origem) — origem: tela | env | ''."""
    s = _settings()
    if s.get(KEY):
        return s[KEY], "tela"
    env = os.getenv("GEMINI_API_KEY", "").strip()
    return env, ("env" if env else "")


def model_name() -> str:
    s = _settings()
    return s.get(MODEL) or os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def is_enabled() -> bool:
    return _settings().get(ENABLED, "1") != "0"


def support_chat() -> Tuple[str, str]:
    s = _settings()
    if s.get(SUPPORT_CHAT):
        return s[SUPPORT_CHAT], "tela"
    env = os.getenv("ASSISTANT_SUPPORT_CHAT_ID", "").strip()
    return env, ("env" if env else "")


def support_bot_token() -> Tuple[str, str]:
    s = _settings()
    if s.get(SUPPORT_BOT):
        return s[SUPPORT_BOT], "tela"
    env = os.getenv("ASSISTANT_SUPPORT_BOT_TOKEN", "").strip()
    return env, ("env" if env else "")


def mask_key(key: str) -> str:
    if not key:
        return ""
    return f"{key[:4]}•••{key[-4:]}" if len(key) > 10 else "•••"


def clean_key(key: str) -> str:
    key = (key or "").strip()
    if not KEY_RE.match(key):
        raise AssistantError("Chave inválida. Copie a chave inteira gerada no Google AI Studio.")
    return key


def clean_model(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    if not MODEL_RE.match(name):
        raise AssistantError("Nome de modelo inválido (ex.: gemini-3.6-flash).")
    return name


def clean_chat(chat: str) -> str:
    chat = (chat or "").strip()
    if chat and not ss.CHAT_RE.match(chat):
        raise AssistantError("Chat ID inválido: só números (o seu ID pessoal no Telegram).")
    return chat


def save(key: str, value: Optional[str], by: str = "") -> None:
    ss.set_value(key, value, by)


# ================================================================== base de conhecimento
_kb_cache: Dict[str, Any] = {"mtime": None, "text": ""}
_kb_lock = threading.Lock()


def knowledge() -> str:
    """Conteúdo de realguard.md (relido sozinho se o arquivo mudar)."""
    with _kb_lock:
        try:
            mtime = os.path.getmtime(KB_PATH)
        except OSError:
            return ""
        if _kb_cache["mtime"] != mtime:
            with open(KB_PATH, encoding="utf-8") as f:
                _kb_cache["text"] = f.read()
            _kb_cache["mtime"] = mtime
        return _kb_cache["text"]


def faq() -> List[Dict[str, str]]:
    """Perguntas frequentes (seção 11 da base): usadas no modo sem internet, sem IA."""
    text = knowledge()
    m = re.search(r"^## 11\..*?$(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return []
    out = []
    for block in re.split(r"\n\s*\n", m.group(1)):
        block = " ".join(line.strip() for line in block.strip().splitlines())
        q = re.match(r"^\*\*(.+?\?)\*\*\s*(.+)$", block)
        if q:
            out.append({"pergunta": q.group(1).strip(), "resposta": q.group(2).strip()})
    return out


# ================================================================== privacidade
_PATTERNS = [
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[documento]"),                     # CPF
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}-?[\dxX]\b"), "[documento]"),                     # RG
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[e-mail]"),
    (re.compile(r"(?<![\w-])(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]\d{4}\b"), "[telefone]"),     # 91234-5678
    (re.compile(r"(?:\+?55\s?)?\(\d{2}\)\s?9?\d{8}\b"), "[telefone]"),                        # (11) 912345678
    (re.compile(r"\b(?:55)?\d{2}9\d{8}\b"), "[telefone]"),                                     # 11912345678
]


def mask_personal(text: str) -> str:
    """Esconde documento, e-mail e telefone antes de qualquer envio para fora (Gemini/Telegram)."""
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


# ================================================================== limites de uso
class RateLimiter:
    def __init__(self, max_calls: int, window_sec: float):
        self.max_calls, self.window = max_calls, window_sec
        self._calls: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int:
        """Registra uma chamada. Devolve 0 se pode, ou os segundos até liberar."""
        now = time.time()
        with self._lock:
            recent = [t for t in self._calls.get(key, []) if now - t < self.window]
            if len(recent) >= self.max_calls:
                self._calls[key] = recent
                return int(self.window - (now - recent[0])) + 1
            recent.append(now)
            self._calls[key] = recent
            return 0


chat_limiter = RateLimiter(10, 60)           # 10 perguntas por minuto por usuário
escalation_limiter = RateLimiter(3, 600)     # 3 pedidos ao especialista a cada 10 min


# ================================================================== conversa
SYSTEM_RULES = """Você é o assistente virtual do RealGuard: o suporte de 1º nível para quem opera o sistema na \
escola (direção, coordenação, secretaria, portaria e segurança). Fale como alguém da equipe RealGuard, de forma \
simpática e objetiva. Nunca diga "segundo a base" ou "o documento diz".

COMO RESPONDER
1. Responda somente com o que está na BASE DE CONHECIMENTO abaixo. Se não estiver nela, diga com franqueza que \
não tem essa informação e marque precisa_escalar = true. Nunca invente telas, botões, números, prazos ou \
funções que a base não cita.
2. Comece pela resposta direta (1 frase). Depois, se for um "como fazer", passos numerados (1., 2., 3.), no \
máximo 7. Prefira respostas curtas (até ~120 palavras), salvo quando a pessoa pedir detalhes.
3. Diga sempre ONDE fica, com o caminho em negrito usando →, escrito exatamente como na base \
(ex.: **Escola → Regras e retenção**, **Alunos → + Novo aluno**). Use **negrito** só para telas, caminhos e \
botões. Sem títulos, tabelas, links ou blocos de código.
4. Leve em conta a tela aberta e o perfil de quem pergunta (informados abaixo). "Aqui", "nesta tela" = a tela \
aberta. Se a ação exige uma permissão que o perfil padrão dessa pessoa não tem (seção 3 da base), avise e \
diga quem pode fazer.
5. Problemas ("não funciona", "não aparece", "deu erro"): siga a seção 9 da base, do passo mais simples ao \
mais complexo. Só marque precisa_escalar = true se a base mandar encaminhar (seção 10), se a solução \
não estiver na base ou se a pessoa pedir para falar com alguém.
6. Pergunta ambígua: faça UMA pergunta curta para entender (precisa_escalar = false).
7. Vocabulário: entenda o jeito como as pessoas falam (seção "Vocabulário" da base): "chamada" = frequência, \
"pai/mãe" = responsável, "bot/zap do bot" = Telegram, "portão/catraca" = câmera, etc.
8. Você ensina a OPERAR o RealGuard. Código, arquivos, servidor, instalação, configuração técnica do \
equipamento ou como o sistema foi construído: diga que é com o suporte técnico e marque precisa_escalar = true.
9. Assunto que não é o RealGuard: recuse com educação em 1 frase e ofereça ajuda com o sistema \
(precisa_escalar = false).
10. Você não vê os dados da escola (alunos, presenças, alertas). Nunca afirme o que aconteceu num caso \
específico; explique as regras e como conferir. Nunca peça senha, token, documento ou dados pessoais.

SUGESTÕES
Em "sugestoes", proponha de 0 a 3 próximas perguntas curtas (até 60 caracteres) que a pessoa provavelmente \
fará em seguida e que a base responde. Não repita a pergunta atual. Lista vazia se não fizer sentido.

Responda SEMPRE em JSON: {"resposta": "<texto>", "precisa_escalar": <true|false>, "sugestoes": ["..."]}"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"resposta": {"type": "STRING"}, "precisa_escalar": {"type": "BOOLEAN"},
                   "sugestoes": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["resposta", "precisa_escalar"],
}


def clean_history(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Normaliza a conversa vinda do navegador: papéis válidos, textos curtos, últimas N, termina no usuário."""
    out: List[Dict[str, str]] = []
    for m in messages or []:
        role = "user" if str(m.get("papel", "")).lower() in ("usuario", "user") else "model"
        text = str(m.get("texto") or "").strip()[:MAX_MESSAGE_CHARS]
        if not text:
            continue
        if out and out[-1]["role"] == role:             # a API não gosta de papéis repetidos em sequência
            out[-1]["text"] = (out[-1]["text"] + "\n" + text)[:MAX_MESSAGE_CHARS * 2]
        else:
            out.append({"role": role, "text": text})
    out = out[-MAX_HISTORY:]
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise AssistantError("Escreva uma pergunta.")
    if len(out[-1]["text"]) > MAX_QUESTION_CHARS:
        raise AssistantError(f"Pergunta muito longa (máx. {MAX_QUESTION_CHARS} caracteres).")
    return out


def _system_prompt(screen: str, role: str = "") -> str:
    ctx = []
    if screen:
        ctx.append(f"Tela aberta agora no painel: {screen[:80]}")
    if role:
        ctx.append(f"Perfil de acesso de quem pergunta: {role[:60]}")
    where = ("\n\nCONTEXTO\n" + "\n".join(ctx)) if ctx else ""
    return f"{SYSTEM_RULES}{where}\n\n===== BASE DE CONHECIMENTO =====\n{knowledge()}"


def _post(model: str, key: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(GEMINI_URL.format(model=model), data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _friendly_http(code: int, detail: str) -> AssistantError:
    low = detail.lower()
    if code in (401, 403) or "api key" in low or "api_key" in low:
        return AssistantError("A chave do Gemini foi recusada. Confira a chave em Escola → Assistente virtual.", 502)
    if code == 404 or "not found" in low:
        return AssistantError("Modelo do Gemini não encontrado. Confira o nome do modelo em Escola → Assistente "
                              "virtual.", 502)
    if code == 429:
        return AssistantError("O limite gratuito do Gemini foi atingido. Tente de novo em instantes.", 503)
    return AssistantError(f"O Gemini não respondeu agora (erro {code}). Tente de novo.", 502)


def call_gemini(system: str, history: List[Dict[str, str]], key: Optional[str] = None,
                model: Optional[str] = None, timeout: float = TIMEOUT_SEC) -> str:
    """Chama o generateContent e devolve o texto da resposta. Erros viram AssistantError."""
    key = key if key is not None else gemini_key()[0]
    model = model or model_name()
    if not key:
        raise AssistantError("O assistente ainda não foi configurado (falta a chave do Gemini).", 503)
    contents = [{"role": h["role"], "parts": [{"text": mask_personal(h["text"])}]} for h in history]
    body: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048,
                             "responseMimeType": "application/json", "responseSchema": RESPONSE_SCHEMA},
    }
    schema_dropped = False
    retries = 0
    while True:
        try:
            data = _post(model, key, body, timeout)
            break
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                detail = ""
            # Modelo que não aceita saída estruturada: tenta uma vez em texto simples.
            if not schema_dropped and e.code == 400 and "api key" not in detail.lower():
                schema_dropped = True
                body["generationConfig"] = {"temperature": 0.2, "maxOutputTokens": 2048}
                continue
            # Gemini ocupado/instável: espera um pouco e tenta de novo, sem o usuário perceber.
            if e.code in TRANSIENT_CODES and retries < len(RETRY_DELAYS):
                logger.info(f"Assistente: Gemini HTTP {e.code}; nova tentativa em {RETRY_DELAYS[retries]:.0f} s.")
                time.sleep(RETRY_DELAYS[retries])
                retries += 1
                continue
            logger.warning(f"Assistente: Gemini HTTP {e.code}: {detail[:200]}")
            raise _friendly_http(e.code, detail) from None
        except Exception as e:  # sem internet, DNS, timeout
            logger.warning(f"Assistente: falha de rede com o Gemini ({type(e).__name__}).")
            raise AssistantError("Sem conexão com o Gemini agora. Veja as perguntas frequentes ou tente de novo.",
                                 503) from None
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return text.strip()


def _suggestions(value: Any) -> List[str]:
    out: List[str] = []
    for item in value if isinstance(value, list) else []:
        text = " ".join(str(item or "").split())[:90]
        if text and text not in out:
            out.append(text)
    return out[:3]


def _answer(d: Dict[str, Any]) -> Dict[str, Any]:
    return {"resposta": str(d["resposta"]).strip()[:4000], "precisa_escalar": bool(d.get("precisa_escalar")),
            "sugestoes": _suggestions(d.get("sugestoes"))}


def parse_answer(raw: str) -> Dict[str, Any]:
    """Lê o JSON do modelo; se vier texto solto, aproveita o texto."""
    if not raw:
        return {"resposta": "Não consegui responder agora. Se precisar, fale com o especialista.",
                "precisa_escalar": True, "sugestoes": []}
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    for candidate in (txt, (re.search(r"\{.*\}", txt, re.S) or [None])[0]):
        if not candidate:
            continue
        try:
            d = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(d, dict) and str(d.get("resposta") or "").strip():
            return _answer(d)
    return {"resposta": txt[:4000], "precisa_escalar": False, "sugestoes": []}


def ask(messages: List[Dict[str, Any]], screen: str = "", role: str = "") -> Dict[str, Any]:
    if not is_enabled():
        raise AssistantError("O assistente está desligado. Fale com a equipe de suporte.", 503)
    history = clean_history(messages)
    return parse_answer(call_gemini(_system_prompt(screen, role), history))


def test_key(key: Optional[str] = None, model: Optional[str] = None) -> str:
    """Pergunta mínima para conferir chave e modelo. Devolve o texto da resposta."""
    raw = call_gemini('Responda só em JSON: {"resposta": "ok", "precisa_escalar": false}',
                      [{"role": "user", "text": "Teste de conexão."}], key=key, model=model, timeout=15)
    return parse_answer(raw)["resposta"]


# ================================================================== escalação
_seq_lock = threading.Lock()


def next_protocol(now: Optional[datetime] = None) -> str:
    """RG-AAAAMMDD-NNNN, sequencial por dia (guardado no banco)."""
    now = now or datetime.now()
    day = now.strftime("%Y%m%d")
    with _seq_lock:
        try:
            cur = json.loads(_settings().get(PROTOCOL_SEQ) or "{}")
        except ValueError:
            cur = {}
        n = int(cur.get("n", 0)) + 1 if cur.get("dia") == day else 1
        ss.set_value(PROTOCOL_SEQ, json.dumps({"dia": day, "n": n}))
    return f"RG-{day}-{n:04d}"


def support_notifier(engine):
    """Bot que envia ao suporte: o próprio do suporte, se houver; senão o da segurança (ou o dos responsáveis)."""
    from app.notifications.telegram import TelegramNotifier
    token = support_bot_token()[0]
    if token:
        return TelegramNotifier(token)
    return getattr(engine, "security_notifier", None) or engine.notifier


def support_bot_is_dedicated(engine) -> bool:
    token = support_bot_token()[0]
    if not token:
        return False
    used = {getattr(engine.notifier, "token", ""), getattr(getattr(engine, "sec_notifier", None), "token", "")}
    return token not in used


def _school_name() -> str:
    try:
        from database import school
        return school.get_settings().get("school_name") or ""
    except Exception:
        return ""


def escalation_text(protocol: str, user: Dict[str, Any], screen: str, note: str,
                    messages: List[Dict[str, Any]], now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    who = user.get("full_name") or user.get("username") or "?"
    role = user.get("role_name") or user.get("role") or ""
    lines = ["🆘 <b>RealGuard · Pedido de suporte</b>",
             f"Protocolo: <b>{escape(protocol)}</b>",
             f"🕒 {now:%d/%m/%Y %H:%M}"]
    school = _school_name()
    if school:
        lines.append(f"🏫 {escape(school)}")
    lines.append(f"👤 {escape(who)}" + (f" · {escape(role)}" if role else "")
                 + (f" · usuário {escape(str(user.get('username')))}" if user.get("username") else ""))
    if screen:
        lines.append(f"📍 Tela: {escape(screen[:80])}")
    if note:
        lines += ["", "<b>O que precisa:</b>", escape(mask_personal(note.strip()[:800]))]
    conv = []
    for m in (messages or [])[-MAX_HISTORY:]:
        text = str(m.get("texto") or "").strip()
        if not text:
            continue
        icon = "👤" if str(m.get("papel", "")).lower() in ("usuario", "user") else "🤖"
        conv.append(f"{icon} {escape(mask_personal(text[:500]))}")
    if conv:
        lines += ["", "<b>Conversa com o assistente:</b>"] + conv
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "…"


def escalate(engine, user: Dict[str, Any], messages: List[Dict[str, Any]], screen: str = "",
             note: str = "") -> str:
    """Envia o pedido ao Telegram do suporte. Devolve o protocolo."""
    chat = support_chat()[0]
    if not chat:
        raise AssistantError("O atendimento com especialista ainda não foi configurado. Avise a equipe de suporte.",
                             503)
    notifier = support_notifier(engine)
    if not notifier or not notifier.enabled:
        raise AssistantError("Nenhum bot do Telegram configurado para enviar o pedido ao suporte.", 503)
    if not (note or "").strip() and not any(str(m.get("texto") or "").strip() for m in messages or []):
        raise AssistantError("Descreva o que você precisa.")
    protocol = next_protocol()
    ok, detail = notifier.send_message(chat, escalation_text(protocol, user, screen, note, messages),
                                       parse_mode="HTML")
    if not ok:
        logger.warning(f"Assistente: pedido {protocol} não chegou ao suporte: {detail}")
        raise AssistantError(f"Não consegui enviar ao suporte agora ({detail}). Tente de novo em instantes.", 502)
    return protocol


def send_support_test(engine, by: str = "") -> None:
    chat = support_chat()[0]
    if not chat:
        raise AssistantError("Informe primeiro o Chat ID de quem recebe os pedidos de suporte.")
    notifier = support_notifier(engine)
    if not notifier or not notifier.enabled:
        raise AssistantError("Nenhum bot do Telegram configurado para enviar ao suporte.")
    ok, detail = notifier.send_message(
        chat, "✅ <b>RealGuard · Assistente virtual</b>\nTeste: os pedidos de suporte vão chegar aqui."
        + (f"\n<i>Enviado por {escape(by)}</i>" if by else ""), parse_mode="HTML")
    if not ok:
        hint = (" Abra o bot no Telegram e toque em Iniciar antes do teste." if "chat not found" in detail.lower()
                or "bot can't initiate" in detail.lower() or "forbidden" in detail.lower() else "")
        raise AssistantError(f"O Telegram recusou: {detail}.{hint}")
