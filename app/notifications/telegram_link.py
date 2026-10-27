"""
Vínculo automático do Telegram — sem precisar descobrir o Chat ID de ninguém.

A Bot API do Telegram NÃO permite um bot procurar alguém pelo número de
telefone (é uma regra de privacidade do Telegram). O que um bot pode fazer
é receber o número quando a PRÓPRIA pessoa o compartilha pelo botão
"Compartilhar meu número". Então o fluxo escalável fica assim:

  1. No cadastro do aluno, a escola informa o celular do responsável.
  2. A escola envia ao responsável o convite do bot (link t.me/<bot>?start=<código>,
     que o dashboard já monta, inclusive para enviar pelo WhatsApp).
  3. O responsável toca no link e em "Iniciar":
       * com o código do convite, o vínculo é feito na hora (link único do aluno);
       * sem código (achou o bot pelo nome), o bot pede "Compartilhar meu número"
         e vincula todos os alunos cujo celular do responsável bate com o número.

Ninguém precisa pegar o celular de ninguém nem copiar Chat ID.

Técnico: uma thread faz long polling no getUpdates (só saída HTTPS, funciona
atrás de NAT, sem webhook/IP público). Nada disso toca o loop de vídeo.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from database import repository

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SEC = 25
# Atrás de proxy (AIBOX na feira: proxy.py no notebook mestre), conexão parada por mais de 10 s é
# derrubada pelo proxy. Long polling curto fica abaixo disso e o painel não acusa erro à toa.
PROXY_POLL_TIMEOUT_SEC = 8
FAILS_BEFORE_WARNING = 3      # falha isolada (rede piscou) não vira aviso no painel


def poll_timeout() -> int:
    import os
    behind_proxy = any((os.getenv(k) or "").strip() for k in ("HTTPS_PROXY", "https_proxy"))
    return PROXY_POLL_TIMEOUT_SEC if behind_proxy else POLL_TIMEOUT_SEC


CONTACT_KEYBOARD = {
    "keyboard": [[{"text": "📱 Compartilhar meu número", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE_KEYBOARD = {"remove_keyboard": True}


def _student_list(students: List[Dict[str, Any]]) -> str:
    from html import escape
    return "\n".join(
        f"• <b>{escape(s['name'])}</b>" + (f" ({escape(s['class_name'])})" if s.get("class_name") else "")
        for s in students)


class TelegramLinker:
    def __init__(self, notifier, on_linked: Optional[Callable[[], None]] = None,
                 repo=repository, school_name: str = "a escola", mode: str = "responsaveis"):
        self.notifier = notifier
        # responsaveis: vínculo dos pais + menu deles (e os alertas, se a segurança não tiver bot próprio)
        # seguranca  : bot só da equipe de segurança (vínculo da conta, botões dos alertas, comandos)
        self.mode = mode
        self.on_linked = on_linked
        self.repo = repo
        self.school_name = school_name
        self._offset: Optional[int] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        # Bot de segurança (botões dos alertas e comandos /pendentes /hoje /historico).
        self.security_bot = None
        # Menu dos responsáveis (presenças, frequência, retiradas, responsáveis...).
        self.guardian_bot = None

    # ------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        if not self.notifier.enabled or (self._thread and self._thread.is_alive() and not self._stop.is_set()):
            return
        # Cada thread tem o próprio sinal de parada: ao trocar o token, a antiga
        # termina sozinha (no fim do long polling) e a nova já começa.
        self._stop = threading.Event()
        self._offset = None
        self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True,
                                        name="telegram-" + self.mode)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def restart(self) -> None:
        self.stop()
        self.last_error = None
        self.start()

    def _run(self, stop: Optional[threading.Event] = None) -> None:
        stop = stop or self._stop
        token = self.notifier.token
        # Webhook ativo impede o getUpdates; o RealGuard não usa webhook.
        self.notifier._call("deleteWebhook", {"drop_pending_updates": False})
        for bot in (self.guardian_bot if self.mode == "responsaveis" else None, self.security_bot):
            if bot is None:
                continue
            try:
                bot.register_commands(self.notifier)
            except Exception as e:
                logger.warning(f"Telegram: não consegui registrar o menu de comandos ({e}).")
        backoff = 2.0
        fails = 0
        while not stop.is_set() and self.notifier.token == token:
            wait = poll_timeout()
            payload = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
            if self._offset is not None:
                payload["offset"] = self._offset
            ok, result = self.notifier._call("getUpdates", payload, timeout=wait + 10)
            if stop.is_set() or self.notifier.token != token:
                return      # token trocado durante a espera: as mensagens ficam para a thread nova
            if not ok:
                fails += 1
                if fails >= FAILS_BEFORE_WARNING or "Conflict" in str(result) or "Unauthorized" in str(result):
                    self.last_error = str(result)
                if "Conflict" in str(result):
                    logger.warning("Telegram: outro processo está lendo este bot (mesmo token rodando em "
                                   "duas máquinas?). O vínculo automático fica só em uma delas.")
                stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            self.last_error = None
            fails = 0
            backoff = 2.0
            for update in result or []:
                self._offset = update.get("update_id", 0) + 1
                try:
                    self.handle_update(update)
                except Exception:
                    logger.exception("Erro ao tratar mensagem do Telegram")

    # ------------------------------------------------------------ regras
    def _reply(self, chat_id, text: str, keyboard: Optional[Dict[str, Any]] = None) -> None:
        self.notifier.send_message(str(chat_id), text, "HTML", reply_markup=keyboard)

    def _menu(self, chat_id: str) -> None:
        if self.guardian_bot is not None:
            try:
                self.guardian_bot.send_menu(chat_id)
            except Exception:
                logger.exception("Menu dos responsáveis não enviado")

    def _security_only(self, msg: Dict[str, Any]) -> Optional[str]:
        """Bot só da segurança: vínculo da conta e comandos; o resto recebe uma explicação curta."""
        chat = msg.get("chat") or {}
        if "id" not in chat or chat.get("type") != "private":
            return None
        if self.security_bot is not None:
            handled = self.security_bot.handle_message(msg)
            if handled:
                return handled
        from html import escape
        from database import access
        user = access.user_by_telegram((msg.get("from") or {}).get("id") or chat["id"])
        if user is not None:
            # já ligado (ex.: tocou em Iniciar de novo): nada de pedir para vincular outra vez
            text = (msg.get("text") or "").strip()
            if text.startswith("/start"):
                return "already_linked"           # a mensagem "Conta ligada" já foi enviada
            self._reply(str(chat["id"]), f"Você está ligado(a) como <b>{escape(user.get('full_name') or user['username'])}</b>. "
                                         "Os alertas chegam aqui com botões.\nComandos: /pendentes · /hoje · /historico")
            return "linked_help"
        self._reply(str(chat["id"]), "Este é o bot da <b>equipe de segurança</b> do RealGuard.\n\n"
                                     "Para receber os alertas aqui, peça ao administrador o QR Code "
                                     "(Administração → Usuários) ou, no RealGuard, toque no seu nome → "
                                     "<b>Vincular Telegram</b>. Responsáveis por alunos usam o outro bot da escola.")
        return "security_bot_help"

    def _linked(self) -> None:
        if self.on_linked:
            try:
                self.on_linked()
            except Exception as e:
                logger.error(f"Falha ao atualizar cache após vínculo: {e}")

    def handle_update(self, update: Dict[str, Any]) -> Optional[str]:
        """Trata uma mensagem recebida. Retorna o que aconteceu (para testes/log)."""
        cq = update.get("callback_query")
        if cq:
            data = str(cq.get("data") or "")
            if data.startswith("gb:") and self.guardian_bot is not None and self.mode == "responsaveis":
                return self.guardian_bot.handle_callback(cq)
            if data.startswith("rg:") and self.security_bot is not None:
                return self.security_bot.handle_callback(cq)
            self.notifier._call("answerCallbackQuery", {"callback_query_id": cq.get("id", "")})
            return None
        msg = update.get("message") or {}
        if self.mode == "seguranca":
            return self._security_only(msg)
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        text0 = (msg.get("text") or "").strip()
        if "id" in chat and text0.startswith("/start"):
            parts0 = text0.split(maxsplit=1)
            code = parts0[1].strip() if len(parts0) > 1 else ""
            if code.startswith("sg"):
                # Convites de segurança (pessoa/grupo) foram aposentados: os alertas vão para o
                # privado de cada conta vinculada no RealGuard.
                self._reply(str(chat["id"]), "Este convite não é mais usado. Os alertas de segurança agora chegam "
                                             "no privado de cada pessoa: no RealGuard, toque no seu nome → "
                                             "<b>Vincular Telegram</b>.")
                return "security_invite_retired"
        if self.security_bot is not None and "id" in chat:
            handled = self.security_bot.handle_message(msg)
            if handled:
                return handled
        if chat.get("type") not in (None, "private") or "id" not in chat:
            return None   # grupos não são usados
        if self.guardian_bot is not None:
            handled = self.guardian_bot.handle_message(msg)
            if handled:
                return handled
        chat_id = str(chat["id"])
        name = " ".join(x for x in [chat.get("first_name"), chat.get("last_name")] if x)
        contact = msg.get("contact")
        self.repo.upsert_telegram_chat(chat_id, name, chat.get("username", ""),
                                       (contact or {}).get("phone_number", "") if contact else "")

        if contact:
            # Segurança: só vale o número da PRÓPRIA conta (não um contato qualquer
            # encaminhado), senão alguém poderia se vincular ao aluno de outra pessoa.
            if contact.get("user_id") != sender.get("id"):
                self._reply(chat_id, "Por segurança, use o botão abaixo para compartilhar o <b>seu</b> número.",
                            CONTACT_KEYBOARD)
                return "contact_rejected"
            students = self.repo.link_telegram_by_phone(contact.get("phone_number", ""), chat_id)
            if not students:
                self._reply(chat_id, "Não encontramos este número no cadastro. Confira com a secretaria "
                                     "se o celular do responsável está correto.", REMOVE_KEYBOARD)
                return "phone_not_found"
            self._linked()
            self._reply(chat_id, "✅ <b>Pronto!</b> Você vai receber os avisos de entrada e saída de:\n"
                        + _student_list(students), REMOVE_KEYBOARD)
            self._menu(chat_id)
            return "linked_phone"

        text = (msg.get("text") or "").strip()
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if token:
                student = self.repo.link_telegram_by_token(token, chat_id)
                if student:
                    self._linked()
                    self._reply(chat_id, "✅ <b>Pronto!</b> Você vai receber os avisos de entrada e saída de:\n"
                                + _student_list(student.get("alunos") or [student]), REMOVE_KEYBOARD)
                    self._menu(chat_id)
                    return "linked_token"
                self._reply(chat_id, "Este convite não é mais válido. Toque no botão abaixo para vincular "
                                     "pelo seu número de celular.", CONTACT_KEYBOARD)
                return "token_invalid"
            self._reply(chat_id, "Olá! Sou o assistente de avisos de presença do RealGuard.\n\n"
                                 "Para receber os avisos do seu filho(a), toque em "
                                 "<b>📱 Compartilhar meu número</b>. Usamos o número só para achar o "
                                 "cadastro feito pela escola.", CONTACT_KEYBOARD)
            return "asked_contact"

        if text:
            self._reply(chat_id, "Para vincular ou revincular os avisos, toque em "
                                 "<b>📱 Compartilhar meu número</b>.", CONTACT_KEYBOARD)
            return "help"
        return None
