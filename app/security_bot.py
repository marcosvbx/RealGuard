"""
Bot de segurança no Telegram: decidir os alertas pelo celular, no privado.

* Sem grupos: os alertas vão para o PRIVADO de cada usuário que ligou a conta do
  RealGuard ao Telegram (seu nome → Vincular Telegram), tem a permissão
  events.view e não desligou "Receber alertas". Desativar o usuário ou tirar a
  permissão interrompe o envio na hora (e os cartões dele somem).
* Cada alerta chega como um cartão com [🔍 Ver foto] [✅ Confirmar] [✖️ Descartar]
  (quem envia/apaga os cartões é security_alerts.SecurityPanel). Quem só pode
  ver (sem events.decide) recebe só o Ver foto.
* Confirmar abre as opções de tipo (Pessoa não identificada, Acesso indevido...).
* A foto só vai para quem toca em Ver foto (LGPD: minimização).
* Decidido por alguém (Telegram ou painel), o cartão some do celular de todos.
  A auditoria registra "via Telegram".
* Comandos: /pendentes, /hoje e /historico, só no privado de quem ligou a conta —
  responsáveis não veem nada disso. Mensagens de grupos são ignoradas.

Tudo pelo mesmo long polling do vínculo (app/notifications/telegram_link.py):
só saída HTTPS, sem domínio, sem webhook e sem custo.
"""

import logging
import os
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional

from app import security_alerts as sa
from app.notifications.messages import CATEGORIES
from database import access, repository
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

COMMANDS = [
    {"command": "pendentes", "description": "Alertas aguardando decisão (com botões)"},
    {"command": "hoje", "description": "Resumo de hoje"},
    {"command": "historico", "description": "Últimas ocorrências confirmadas"},
]

NOT_LINKED = ("Sua conta do Telegram ainda não está ligada a um usuário do RealGuard. "
              "No RealGuard, toque no seu nome (canto superior) → Vincular Telegram.")
NO_PERMISSION = "Você não tem permissão para decidir alertas."
NO_VIEW = "Você não tem permissão para ver as ocorrências."


def _hhmm(value: Any) -> str:
    return str(value or "")[11:16]


def _ddmm(value: Any) -> str:
    v = str(value or "")
    return f"{v[8:10]}/{v[5:7]}" if len(v) >= 10 else ""


def _who(user: Dict[str, Any]) -> str:
    return user.get("full_name") or user.get("username") or "operador"


class SecurityBot:
    def __init__(self, engine):
        self.engine = engine

    @property
    def notifier(self):
        return sa.sec_notifier(self.engine)

    def _call(self, method: str, payload: Dict[str, Any]):
        return self.notifier._call(method, payload)

    # ------------------------------------------------------------ menu de comandos
    def register_commands(self, notifier=None) -> int:
        """Menu "/" só no privado de quem ligou a conta (escopo padrão fica vazio: responsáveis não veem)."""
        n = 0
        self._call("deleteMyCommands", {"scope": {"type": "all_group_chats"}})   # versões antigas usavam grupos
        for u in access.linked_telegram_users():
            n += int(self.set_user_commands(u["telegram_user_id"], True))
        return n

    def set_user_commands(self, telegram_user_id: str, on: bool) -> bool:
        scope = {"type": "chat", "chat_id": str(telegram_user_id)}
        if on:
            ok, _ = self._call("setMyCommands", {"commands": COMMANDS, "scope": scope})
        else:
            ok, _ = self._call("deleteMyCommands", {"scope": scope})
        return bool(ok)

    # ------------------------------------------------------------ mensagens (comandos e vínculo)
    def handle_message(self, msg: Dict[str, Any]) -> Optional[str]:
        """Trata /start us… e os comandos da segurança. None = não é com este bot (segue o fluxo dos responsáveis)."""
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        text = (msg.get("text") or "").strip()
        if "id" not in chat or not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        cmd = parts[0][1:].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        chat_id = str(chat["id"])
        private = chat.get("type") == "private"
        if not private:
            return None             # grupos não são usados: alertas e comandos só no privado
        if cmd == "start" and arg.startswith("us"):
            return self._link_user(chat_id, sender, arg)
        if cmd not in ("pendentes", "hoje", "historico", "histórico"):
            return None
        user = access.user_by_telegram(sender.get("id"))
        if user is None or "events.view" not in user["permissions"]:
            self._send(chat_id, NOT_LINKED if user is None else NO_VIEW)
            return "command_denied"
        if cmd == "pendentes":
            pending = sa.pending_today()
            self._send(chat_id, sa.panel_text(), sa.pending_keyboard(pending, "events.decide" in user["permissions"]))
            return "cmd_pendentes"
        if cmd == "hoje":
            self._send(chat_id, self.today_text())
            return "cmd_hoje"
        self._send(chat_id, self.history_text())
        return "cmd_historico"

    def _send(self, chat_id: str, text: str, keyboard: Optional[Dict[str, Any]] = None) -> None:
        payload = {"chat_id": str(chat_id), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = keyboard
        self._call("sendMessage", payload)

    def _link_user(self, chat_id: str, sender: Dict[str, Any], code: str) -> str:
        name = " ".join(x for x in [sender.get("first_name"), sender.get("last_name")] if x) or sender.get("username") or ""
        user = access.link_telegram_user(code, str(sender.get("id") or chat_id), name)
        if not user:
            self._send(chat_id, "Este link de vínculo não vale mais (ele expira em 15 minutos). "
                                "Gere outro no RealGuard: seu nome → Vincular Telegram.")
            return "user_link_invalid"
        access.log_audit("telegram_conta_vinculada", user=user, target_type="usuario", target_id=user["id"],
                         detail=f"Telegram de {name or chat_id}")
        perms = user["permissions"]
        if "events.view" not in perms:
            self._send(chat_id, f"✅ <b>Conta ligada:</b> {escape(_who(user))}\n\n"
                                "Seu perfil não tem acesso às ocorrências, então você não recebe alertas de segurança.")
            return "user_linked"
        self.set_user_commands(user["telegram_user_id"], True)
        alerts = access.telegram_status(user["id"])["alertas"]
        self._send(chat_id, f"✅ <b>Conta ligada:</b> {escape(_who(user))}\n\n"
                   + ("Os alertas de segurança vão chegar aqui. " if alerts else
                      "Os alertas estão desligados para você (ligue em Vincular Telegram, no RealGuard). ")
                   + ("Use os botões para ver a foto, confirmar ou descartar. " if "events.decide" in perms
                      else "Seu perfil pode ver os alertas e as fotos, mas não decidir. ")
                   + "\nComandos: /pendentes · /hoje · /historico")
        sa.request_panel_update()       # já manda os cartões e a lista de hoje
        return "user_linked"

    # ------------------------------------------------------------ textos dos comandos
    def today_text(self, now: Optional[datetime] = None) -> str:
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            ev = [dict(r) for r in conn.execute(
                "SELECT status, category, created_at, validated_at FROM security_events "
                "WHERE date(created_at) = ? AND ficticio = 0", (today,))]
            mov = {r[0]: r[1] for r in conn.execute(
                "SELECT direction, COUNT(*) FROM presences WHERE date(created_at) = ? GROUP BY direction", (today,))}
        conf = [e for e in ev if e["status"] == "VALIDATED_TRUE"]
        resp = []
        for e in ev:
            if e["status"] in ("VALIDATED_TRUE", "VALIDATED_FALSE") and e.get("validated_at"):
                try:
                    resp.append((datetime.strptime(e["validated_at"][:19], "%Y-%m-%d %H:%M:%S")
                                 - datetime.strptime(e["created_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60)
                except ValueError:
                    pass
        lines = [f"📊 <b>RealGuard · Resumo de hoje ({now:%d/%m})</b>", "",
                 f"🚶 Movimento: {mov.get('ENTRY', 0)} entradas · {mov.get('EXIT', 0)} saídas", "",
                 f"⚠️ Alertas de rosto obstruído: <b>{len(ev)}</b>",
                 f"  🔴 confirmados: {len(conf)}",
                 f"  ✔️ descartados pela portaria: {sum(1 for e in ev if e['status'] == 'VALIDATED_FALSE')}",
                 f"  🤖 descartados sozinhos (o rosto apareceu): {sum(1 for e in ev if e['status'] == 'AUTO_DISMISSED')}",
                 f"  ⏳ aguardando: {sum(1 for e in ev if e['status'] == 'PENDING')}"]
        if resp:
            lines.append(f"⏱️ Tempo médio de decisão: {round(sum(resp) / len(resp))} min")
        if conf:
            tipos: Dict[str, int] = {}
            for e in conf:
                k = CATEGORIES.get(e.get("category") or "", "sem tipo")
                tipos[k] = tipos.get(k, 0) + 1
            lines.append("Tipos: " + " · ".join(f"{escape(k)} ({v})" for k, v in sorted(tipos.items(), key=lambda t: -t[1])))
        return "\n".join(lines)

    def history_text(self, limit: int = 10) -> str:
        with get_db_connection() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT se.id, se.created_at, se.category, se.note, se.decided_via, u.full_name AS quem, "
                "c.name AS camera FROM security_events se LEFT JOIN users u ON u.id = se.validated_by "
                "LEFT JOIN cameras c ON c.id = se.camera_id WHERE se.status = 'VALIDATED_TRUE' AND se.ficticio = 0 "
                "ORDER BY se.id DESC LIMIT ?", (int(limit),))]
        if not rows:
            return "🗂️ <b>Histórico</b>\n\nNenhuma ocorrência confirmada ainda."
        lines = ["🗂️ <b>Últimas ocorrências confirmadas</b>", ""]
        for r in rows:
            cat = CATEGORIES.get(r.get("category") or "", "sem tipo")
            via = " pelo Telegram" if r.get("decided_via") == "telegram" else ""
            lines.append(f"🔴 {_ddmm(r['created_at'])} {_hhmm(r['created_at'])} · <b>{escape(cat)}</b> · "
                         f"{escape(r.get('camera') or 'câmera')}"
                         + (f"\n    por {escape(r['quem'])}{via}" if r.get("quem") else ""))
            if r.get("note"):
                lines.append(f"    📝 {escape(r['note'][:120])}")
        return "\n".join(lines)

    # ------------------------------------------------------------ botões
    def _answer(self, cq: Dict[str, Any], text: str = "", alert: bool = False) -> None:
        payload = {"callback_query_id": cq.get("id", "")}
        if text:
            payload["text"] = text[:190]
            payload["show_alert"] = bool(alert)
        self._call("answerCallbackQuery", payload)

    def _set_markup(self, msg: Dict[str, Any], keyboard: Optional[Dict[str, Any]]) -> None:
        chat = (msg.get("chat") or {}).get("id")
        if chat is None or not msg.get("message_id"):
            return
        self._call("editMessageReplyMarkup", {"chat_id": chat, "message_id": msg["message_id"],
                                              "reply_markup": keyboard or {"inline_keyboard": []}})

    def _refresh_list(self, msg: Dict[str, Any], decide: bool = True) -> None:
        """Mensagem de lista (fixada ou /pendentes): reescreve com o que ainda falta decidir."""
        chat = (msg.get("chat") or {}).get("id")
        if chat is None or not msg.get("message_id"):
            return
        payload = {"chat_id": chat, "message_id": msg["message_id"], "text": sa.panel_text(), "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        kb = sa.pending_keyboard(sa.pending_today(), decide)
        if kb:
            payload["reply_markup"] = kb
        self._call("editMessageText", payload)

    def _after_decided(self, msg: Dict[str, Any], src: str, decide: bool = True) -> None:
        if src == "p":
            self._refresh_list(msg, decide)
        elif src == "v":
            self._set_markup(msg, None)
        # cartões (c): a thread do painel apaga em todos os chats
        sa.request_panel_update()

    def _event(self, event_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            r = conn.execute("SELECT se.*, u.full_name AS quem, c.name AS camera FROM security_events se "
                             "LEFT JOIN users u ON u.id = se.validated_by LEFT JOIN cameras c ON c.id = se.camera_id "
                             "WHERE se.id = ?", (int(event_id),)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _decided_text(ev: Dict[str, Any]) -> str:
        who = f" por {ev['quem']}" if ev.get("quem") else ""
        if ev["status"] == "VALIDATED_TRUE":
            return f"Este alerta já foi confirmado{who}."
        if ev["status"] == "VALIDATED_FALSE":
            return f"Este alerta já foi descartado{who}."
        return "Este alerta já foi descartado automaticamente (o rosto apareceu)."

    def handle_callback(self, cq: Dict[str, Any]) -> str:
        data = str(cq.get("data") or "")
        msg = cq.get("message") or {}
        parts = data.split(":")
        if len(parts) < 4 or parts[0] != "rg" or not parts[2].isdigit():
            self._answer(cq)
            return "ignored"
        action, event_id, src = parts[1], int(parts[2]), parts[3]
        user = access.user_by_telegram((cq.get("from") or {}).get("id"))
        if user is None:
            self._answer(cq, NOT_LINKED, alert=True)
            return "not_linked"
        perms = user["permissions"]
        ev = self._event(event_id)
        if ev is None:
            self._answer(cq, "Alerta não encontrado.", alert=True)
            return "not_found"

        if action == "f":
            if "events.view" not in perms:
                self._answer(cq, NO_VIEW, alert=True)
                return "no_permission"
            return self._send_photo(cq, user, ev)

        if "events.decide" not in perms:
            self._answer(cq, NO_PERMISSION, alert=True)
            return "no_permission"
        if ev["status"] != "PENDING":
            self._answer(cq, self._decided_text(ev), alert=True)
            self._after_decided(msg, src)
            return "already_decided"

        if action == "c":
            self._set_markup(msg, sa.types_keyboard(event_id, src))
            self._answer(cq, "O que foi? Escolha o tipo.")
            return "ask_type"
        if action == "b":
            self._set_markup(msg, sa.pending_keyboard(sa.pending_today()) if src == "p"
                             else sa.card_keyboard(event_id, src))      # só chega aqui quem pode decidir
            self._answer(cq)
            return "back"
        if action in ("t", "d"):
            status = "VALIDATED_TRUE" if action == "t" else "VALIDATED_FALSE"
            category = parts[4] if action == "t" and len(parts) > 4 else ""
            if action == "t" and category not in CATEGORIES:
                category = "outro"
            result, _ = sa.decide(self.engine, event_id, status, user, category, via="telegram")
            if result == repository.DECIDE_ALREADY:
                ev = self._event(event_id) or ev
                self._answer(cq, self._decided_text(ev), alert=True)
                self._after_decided(msg, src)
                return "already_decided"
            if result != repository.DECIDE_OK:
                self._answer(cq, "Não consegui registrar a decisão. Tente pelo RealGuard.", alert=True)
                return "error"
            access.log_audit("ocorrencia_decidida", user=user, target_type="evento", target_id=event_id,
                             detail=f"{status} · {CATEGORIES[category]} (via Telegram)" if category
                             else f"{status} (via Telegram)")
            self._answer(cq, f"🔴 Confirmado: {CATEGORIES[category]}" if action == "t" else "✔️ Alerta descartado")
            self._after_decided(msg, src)
            return "confirmed" if action == "t" else "discarded"
        self._answer(cq)
        return "ignored"

    def _send_photo(self, cq: Dict[str, Any], user: Dict[str, Any], ev: Dict[str, Any]) -> str:
        path = repository.event_snapshot_path(ev["id"])
        if not os.path.isfile(path):
            self._answer(cq, "Este alerta não tem foto.", alert=True)
            return "no_photo"
        with open(path, "rb") as f:
            jpeg = f.read()
        private = str((cq.get("from") or {}).get("id"))
        caption = (f"🔍 <b>Alerta nº {ev['id']}</b> · {escape(ev.get('camera') or 'câmera')} · "
                   f"{_ddmm(ev['created_at'])} {_hhmm(ev['created_at'])}\n<i>Foto só para você. Não encaminhe.</i>")
        keyboard = sa.card_keyboard(ev["id"], "v") if ev["status"] == "PENDING" and "events.decide" in user["permissions"] else None
        ok, detail = self.notifier.send_photo(private, jpeg, caption, "HTML", reply_markup=keyboard)
        if not ok:
            self._answer(cq, "Não consegui te mandar no privado. Abra uma conversa comigo (toque no meu nome → "
                             "Iniciar) e tente de novo.", alert=True)
            return "photo_failed"
        access.log_audit("ocorrencia_foto_vista", user=user, target_type="evento", target_id=ev["id"],
                         detail="foto enviada no privado (via Telegram)")
        self._answer(cq, "📩 Mandei a foto no seu privado.")
        return "photo_sent"
