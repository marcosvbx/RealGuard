"""
Configurações do sistema guardadas no banco (editáveis pela tela).

Hoje: integração com o Telegram. O que estiver salvo aqui tem prioridade
sobre o config/realguard.env; o .env continua valendo como reserva.

Atenção: o token do bot fica no banco — portanto também nos backups.
Ele nunca volta inteiro para a tela (só os últimos caracteres).
"""

import re
from typing import Any, Dict, List, Optional

from database.connection import get_db_connection

TG_TOKEN = "telegram_token"
TG_SEC_TOKEN = "telegram_security_token"     # bot só da segurança (vazio = usa o dos responsáveis)
TG_GUARDIANS = "telegram_guardians_enabled"
TG_SECURITY = "telegram_security_chat_ids"
TG_TEMPLATE = "telegram_template"

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
CHAT_RE = re.compile(r"^-?\d{3,20}$")
TEMPLATE_FIELDS = ("nome", "acao", "hora", "data", "camera", "rm")


class SettingsError(ValueError):
    pass


def get_all() -> Dict[str, str]:
    try:
        with get_db_connection() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT key, value FROM system_settings")}
    except Exception:
        return {}      # banco ainda sem a migração 6


def get_meta(key: str) -> Dict[str, str]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT updated_at, updated_by FROM system_settings WHERE key = ?", (key,)).fetchone()
    return {"em": row[0], "por": row[1]} if row else {}


def set_value(key: str, value: Optional[str], by: str = "") -> None:
    """value None apaga a chave (volta a valer o .env)."""
    with get_db_connection() as conn:
        if value is None:
            conn.execute("DELETE FROM system_settings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT INTO system_settings (key, value, updated_at, updated_by) "
                         "VALUES (?, ?, datetime('now','localtime'), ?) ON CONFLICT(key) DO UPDATE SET "
                         "value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                         (key, value, (by or "")[:64]))
        conn.commit()


# ------------------------------------------------------------------ validação
def clean_token(token: str) -> str:
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise SettingsError("Token inválido. Ele tem o formato 123456789:ABC… e vem do @BotFather.")
    return token


def clean_chat_ids(values: List[str]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        if not CHAT_RE.match(v):
            raise SettingsError(f"Chat ID inválido: {v[:24]} (só números; grupos começam com -100).")
        if v not in out:
            out.append(v)
    return out[:20]


def clean_template(text: str) -> str:
    text = (text or "").strip()
    if len(text) > 500:
        raise SettingsError("Mensagem muito longa (máx. 500 caracteres).")
    if text:
        try:
            text.format(**{k: "x" for k in TEMPLATE_FIELDS})
        except (KeyError, IndexError, ValueError) as e:
            raise SettingsError(f"Campo desconhecido na mensagem: {e}. Use {{nome}}, {{acao}}, {{hora}}, "
                                "{data}, {camera} ou {rm}.")
    return text


def mask_token(token: str) -> str:
    if not token:
        return ""
    head = token.split(":", 1)[0]
    return f"{head[:3]}•••:•••{token[-4:]}"


# ------------------------------------------------------------------ valores efetivos
def telegram_config(env_token: str = "", env_security: Optional[List[str]] = None,
                    env_template: str = "", env_security_token: str = "") -> Dict[str, Any]:
    """Junta banco + .env. Origem de cada valor vai junto (para a tela explicar)."""
    s = get_all()
    token = s.get(TG_TOKEN, "") or env_token
    sec_token = s.get(TG_SEC_TOKEN, "") or env_security_token
    if TG_SECURITY in s:
        security, sec_src = [c for c in s[TG_SECURITY].split(",") if c], "tela"
    else:
        security, sec_src = list(env_security or []), "env"
    template = s[TG_TEMPLATE] if TG_TEMPLATE in s else env_template
    return {
        "token": token,
        "token_origem": "tela" if s.get(TG_TOKEN) else ("env" if env_token else ""),
        "sec_token": sec_token,
        "sec_token_origem": "tela" if s.get(TG_SEC_TOKEN) else ("env" if env_security_token else ""),
        "avisos_responsaveis": s.get(TG_GUARDIANS, "1") != "0",
        "seguranca": security,
        "seguranca_origem": sec_src,
        "modelo": template,
    }


# ------------------------------------------------------------------ convites de segurança
TG_INVITES = "telegram_security_invites"
TG_NAMES = "telegram_security_names"
INVITE_HOURS = 24


def _json(key: str, default):
    import json
    raw = get_all().get(key)
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def _save_json(key: str, value, by: str = "") -> None:
    import json
    set_value(key, json.dumps(value, ensure_ascii=False), by)


def security_names() -> Dict[str, str]:
    return _json(TG_NAMES, {})


def create_security_invite(kind: str, by: str = "") -> Dict[str, Any]:
    """Código de uso único (24 h) para entrar na lista de alertas de segurança pelo próprio Telegram."""
    import secrets
    from datetime import datetime, timedelta
    now = datetime.now()
    invites = [i for i in _json(TG_INVITES, []) if i.get("expira", "") > now.strftime("%Y-%m-%d %H:%M:%S")]
    code = "sg" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:14]
    invite = {"codigo": code, "tipo": "grupo" if kind == "grupo" else "pessoa", "por": by[:64],
              "expira": (now + timedelta(hours=INVITE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")}
    invites.append(invite)
    _save_json(TG_INVITES, invites[-20:], by)
    return invite


def use_security_invite(code: str, chat_id: str, name: str, current: List[str]) -> Optional[Dict[str, Any]]:
    """Consome o código (uma vez só) e põe o chat na lista. Devolve o convite usado ou None."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    invites = _json(TG_INVITES, [])
    found = next((i for i in invites if i.get("codigo") == code and i.get("expira", "") > now), None)
    if not found:
        return None
    _save_json(TG_INVITES, [i for i in invites if i.get("codigo") != code and i.get("expira", "") > now])
    ids = clean_chat_ids(list(current or []) + [str(chat_id)])
    set_value(TG_SECURITY, ",".join(ids), "telegram")
    names = security_names()
    names[str(chat_id)] = (name or "")[:60]
    _save_json(TG_NAMES, names)
    return found
