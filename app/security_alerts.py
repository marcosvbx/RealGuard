"""
Ocorrências de segurança no Telegram, em duas etapas.

1. Detecção: aviso CURTO, sem foto ("possível rosto obstruído às 10:42 —
   confirme no RealGuard"). A maioria dos alertas acaba descartada; não mandar
   a foto de alguém que talvez não fez nada para um serviço externo é o que a
   LGPD pede (minimização).
2. Decisão humana no RealGuard:
   * confirmada  -> relatório completo (tipo, observação, quem e quando) COM a foto;
   * descartada  -> uma linha ("descartado por Fulano"), para ninguém conferir de novo.
3. Lembrete: alerta parado sem decisão por N minutos (Escola → Integrações →
   Telegram; 0 desliga) gera um único "ainda aguardando decisão".

Com o bot de segurança (app/security_bot.py) cada alerta vira um CARTÃO no
privado de cada pessoa que recebe (contas vinculadas, ver recipients()) com os
botões [Ver foto] [Confirmar] [Descartar]. A decisão vale igual pelo Telegram ou
pelo painel (função decide() abaixo, usada pelos dois) e, decidido, o cartão
some do celular de todos e a lista fixada é atualizada. Quem envia e
apaga os cartões é a thread do painel (sync_cards), nunca o loop de vídeo.
"""

import json
import logging
import os
import threading
import time
from datetime import timedelta
from config.clock import datetime
from typing import Any, Dict, Optional, Tuple

from app.notifications.messages import (
    CATEGORIES, security_card_message, security_confirmed_message, security_discarded_message,
    security_reminder_message,
)
from database.connection import get_db_connection

logger = logging.getLogger(__name__)

REMINDER_KEY = "telegram_reminder_min"
DEFAULT_REMINDER_MIN = 5


def dashboard_link() -> str:
    """Endereço da tela de Ocorrências (só abre de dentro da rede da escola)."""
    base = os.getenv("REALGUARD_URL", "").strip().rstrip("/")
    return f"{base}/#ocorrencias" if base else ""


def reminder_minutes() -> int:
    from database import system_settings
    try:
        return max(0, min(240, int(system_settings.get_all().get(REMINDER_KEY, DEFAULT_REMINDER_MIN))))
    except (TypeError, ValueError):
        return DEFAULT_REMINDER_MIN


def _event(event_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM security_events WHERE id = ?", (int(event_id),)).fetchone()
    return dict(r) if r else None


def sec_notifier(engine):
    """O bot da segurança (token próprio) ou, sem ele, o bot dos responsáveis."""
    return getattr(engine, "security_notifier", None) or engine.notifier


def recipients(engine) -> list:
    """
    Quem recebe os alertas: só o PRIVADO de cada usuário ativo que ligou a conta ao
    Telegram, tem a permissão events.view e não desligou os alertas. Sem grupos:
    desativar o usuário (ou tirar a permissão) para o envio na hora.
    """
    if not sec_notifier(engine).enabled:
        return []
    from database import access
    return access.alert_recipients()


def _targets(engine) -> list:
    return [r["chat"] for r in recipients(engine)]


def save_decision_details(event_id: int, category: str, note: str) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE security_events SET category = ?, note = ? WHERE id = ?",
                     ((category or "")[:40], (note or "").strip()[:500], int(event_id)))
        conn.commit()


def _cards(ev: Optional[Dict[str, Any]]) -> Dict[str, int]:
    try:
        data = json.loads((ev or {}).get("tg_msgs") or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def decide(engine, event_id: int, status: str, user: Dict[str, Any], category: str = "", note: str = "",
           via: str = "painel") -> Tuple[str, int]:
    """
    Decisão humana — a MESMA para o painel e para o bot do Telegram. Devolve
    (resultado de repository.decide_event, quantas mensagens foram ao Telegram). Quem
    chama confere a permissão events.decide antes e grava a auditoria.
    """
    from database import repository
    category = (category or "").strip()
    if status == "VALIDATED_TRUE" and category not in CATEGORIES:
        category = "outro"
    result = repository.decide_event(event_id, status, user.get("id"))
    if result != repository.DECIDE_OK:
        return result, 0
    with get_db_connection() as conn:
        conn.execute("UPDATE security_events SET decided_via = ? WHERE id = ?",
                     ("telegram" if via == "telegram" else "painel", int(event_id)))
        conn.commit()
    if status == "VALIDATED_TRUE":
        save_decision_details(event_id, category, note)
    sent = 0
    try:
        sent = notify_decision(engine, event_id, user)
    except Exception as e:      # o Telegram nunca impede a decisão
        logger.error(f"Relatório da ocorrência {event_id} não enviado: {e}")
    request_panel_update()      # apaga os cartões e atualiza a lista fixada
    return result, sent


def decision_label(status: str, category: str = "") -> str:
    if status == "VALIDATED_TRUE":
        return "confirmada · " + CATEGORIES.get(category or "", category or "Outro")
    return "descartada" if status == "VALIDATED_FALSE" else status


# ---------------------------------------------------------------- botões (callback_data ≤ 64 bytes)
# rg:<ação>:<alerta>:<origem>[:<tipo>]   ação: f=foto c=confirmar t=tipo d=descartar b=voltar
# origem: c=cartão do alerta · p=lista (fixada ou /pendentes) · v=foto no privado
def card_keyboard(event_id: int, src: str = "c", decide: bool = True) -> Optional[Dict[str, Any]]:
    """Botões do cartão. Quem só pode ver (sem events.decide) recebe só o Ver foto."""
    row = [{"text": "✅ Confirmar", "callback_data": f"rg:c:{event_id}:{src}"},
           {"text": "✖️ Descartar", "callback_data": f"rg:d:{event_id}:{src}"}] if decide else []
    if src != "v":
        row.insert(0, {"text": "🔍 Ver foto", "callback_data": f"rg:f:{event_id}:{src}"})
    return {"inline_keyboard": [row]} if row else None


def types_keyboard(event_id: int, src: str = "c") -> Dict[str, Any]:
    rows = [[{"text": name, "callback_data": f"rg:t:{event_id}:{src}:{key}"}] for key, name in CATEGORIES.items()]
    rows.append([{"text": "↩️ Voltar", "callback_data": f"rg:b:{event_id}:{src}"}])
    return {"inline_keyboard": rows}


PANEL_MAX_ROWS = 8


def pending_today(now: Optional[datetime] = None, limit: int = 50) -> list:
    now = now or datetime.now()
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT se.*, c.name AS camera FROM security_events se LEFT JOIN cameras c ON c.id = se.camera_id "
            "WHERE se.status = 'PENDING' AND se.ficticio = 0 AND date(se.created_at) = ? ORDER BY se.id LIMIT ?",
            (now.strftime("%Y-%m-%d"), int(limit)))]


def pending_keyboard(pending: list, decide: bool = True) -> Optional[Dict[str, Any]]:
    """Uma linha de botões por alerta aguardando decisão (lista fixada e /pendentes)."""
    rows = []
    for r in pending[:PANEL_MAX_ROWS]:
        hh, eid = str(r.get("created_at") or "")[11:16], r["id"]
        row = [{"text": f"🔍 {hh}" if decide else f"🔍 Ver foto das {hh}", "callback_data": f"rg:f:{eid}:p"}]
        if decide:
            row += [{"text": "✅ Confirmar", "callback_data": f"rg:c:{eid}:p"},
                    {"text": "✖️ Descartar", "callback_data": f"rg:d:{eid}:p"}]
        rows.append(row)
    return {"inline_keyboard": rows} if rows else None


def notify_decision(engine, event_id: int, user: Dict[str, Any]) -> int:
    """Depois da decisão humana: relatório completo (confirmada) ou uma linha (descartada). Devolve quantos envios."""
    targets = _targets(engine)
    ev = _event(event_id)
    if not targets or not ev or ev.get("ficticio"):
        return 0
    by = user.get("full_name") or user.get("username") or "operador"
    if ev["status"] == "VALIDATED_TRUE":
        from database import repository
        photo = None
        path = repository.event_snapshot_path(event_id)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                photo = f.read()
        cam = engine.settings.camera_name
        try:
            c = repository.get_camera(ev.get("camera_id") or 0)
            cam = (c or {}).get("name") or cam
        except Exception:
            pass
        text = security_confirmed_message(ev, cam, by)
    elif ev["status"] == "VALIDATED_FALSE":
        if _cards(ev):
            return 0        # o cartão some e a lista fixada mostra "descartado por…": sem mensagem extra
        photo, text = None, security_discarded_message(ev, by)
    else:
        return 0
    for chat in targets:
        sec_notifier(engine).enqueue(chat, text, None, photo=photo, parse_mode="HTML")
    request_panel_update()
    return len(targets)


class ReminderLoop:
    """Confere a cada 30 s se há alerta pendente há mais de N minutos (um lembrete por alerta)."""
    EVERY_SEC = 30

    def __init__(self, engine):
        self.engine = engine
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="lembrete-ocorrencias")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.EVERY_SEC):
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Lembrete de ocorrências falhou: {e}")

    def run_once(self, now: Optional[datetime] = None) -> int:
        minutes = reminder_minutes()
        targets = _targets(self.engine)
        if not minutes or not targets:
            return 0
        now = now or datetime.now()
        limit = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        today = now.strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM security_events WHERE status = 'PENDING' AND reminded_at IS NULL AND ficticio = 0 "
                "AND created_at <= ? AND date(created_at) = ? ORDER BY id", (limit, today))]
            for r in rows:
                conn.execute("UPDATE security_events SET reminded_at = ? WHERE id = ?",
                             (now.strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
            conn.commit()
        bumped = False
        for r in rows:
            if PANEL is not None and _cards(r):
                # Com cartões: apaga o cartão antigo e manda de novo (lá embaixo, com "ainda aguardando").
                PANEL.drop_cards(r, notify_again=True)
                bumped = True
                continue
            text = security_reminder_message(r, minutes, dashboard_link())
            for chat in targets:
                sec_notifier(self.engine).enqueue(chat, text, None, parse_mode="HTML")
        if bumped:
            request_panel_update()
        return len(rows)


# ======================================================================
# Painel fixo no Telegram: UMA mensagem por chat de segurança, fixada no
# topo e editada a cada mudança (novo alerta, confirmação, descarte). Editar
# não gera notificação — quem avisa é o aviso curto de cada alerta; o painel
# é a "lista" sempre atualizada do que ainda falta decidir hoje.
# ======================================================================
PANEL_KEY = "telegram_panel"            # "1" liga (padrão) / "0" desliga
PANEL_MSGS_KEY = "telegram_panel_msgs"  # {chat_id: {"id": message_id, "dia": "AAAA-MM-DD"}}
PANEL: Optional["SecurityPanel"] = None


def panel_enabled() -> bool:
    from database import system_settings
    return system_settings.get_all().get(PANEL_KEY, "1") != "0"


def request_panel_update() -> None:
    """Chamado de qualquer lugar (motor, API): agenda a atualização do painel, sem bloquear."""
    if PANEL is not None:
        PANEL.request_update()


def panel_text(now: Optional[datetime] = None) -> str:
    from html import escape
    from app.notifications.messages import CATEGORIES
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT se.id, se.status, se.created_at, se.validated_at, se.category, se.camera_id, "
            "u.full_name AS quem, c.name AS camera FROM security_events se "
            "LEFT JOIN users u ON u.id = se.validated_by LEFT JOIN cameras c ON c.id = se.camera_id "
            "WHERE date(se.created_at) = ? AND se.ficticio = 0 ORDER BY se.id", (today,))]
    pending = [r for r in rows if r["status"] == "PENDING"]
    decided = [r for r in rows if r["status"] != "PENDING"][-5:]
    lines = ["📌 <b>RealGuard · Alertas de segurança de hoje</b>", ""]
    if pending:
        lines.append(f"⏳ <b>{len(pending)} aguardando decisão</b>")
        for r in pending:
            mins = int((now - datetime.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
            lines.append(f"• {r['created_at'][11:16]} · {escape(r.get('camera') or 'câmera')} · há {max(0, mins)} min")
    else:
        lines.append("✅ Nenhum alerta aguardando decisão.")
    if decided:
        lines += ["", "<b>Últimas decisões</b>"]
        for r in reversed(decided):
            who = escape(r.get("quem") or "")
            if r["status"] == "VALIDATED_TRUE":
                cat = escape(CATEGORIES.get(r.get("category") or "", "ocorrência"))
                lines.append(f"🔴 {r['created_at'][11:16]} confirmado · {cat}" + (f" · {who}" if who else ""))
            elif r["status"] == "VALIDATED_FALSE":
                lines.append(f"✔️ {r['created_at'][11:16]} descartado" + (f" · {who}" if who else ""))
            else:
                lines.append(f"✔️ {r['created_at'][11:16]} descartado automaticamente (o rosto apareceu)")
    link = dashboard_link()
    if len(pending) > PANEL_MAX_ROWS:
        lines.append(f"<i>+{len(pending) - PANEL_MAX_ROWS} sem botão aqui: use /pendentes ou o RealGuard.</i>")
    tip = "Use os botões abaixo ou a tela de Ocorrências." if pending else "Nada para decidir agora."
    lines += ["", f"<i>Atualizado às {now:%H:%M}. {tip}</i>"] + ([escape(link)] if link else [])
    return "\n".join(lines)


class SecurityPanel:
    """
    Mantém, em cada chat de segurança, a mensagem fixada (lista de pendências
    com botões) e os cartões de cada alerta. Roda numa thread própria: o
    Telegram pode ser lento e nada disso pode travar o vídeo.
    """
    DEBOUNCE_SEC = 2.0
    REFRESH_SEC = 300       # com pendências, reedita a cada 5 min (atualiza o "há X min")
    QUIET_SEC = 60          # vários alertas seguidos: só o 1º cartão toca no celular
    MAX_CARDS = 20          # nunca inunda o grupo (ex.: depois de muito tempo sem internet)

    def __init__(self, engine):
        self.engine = engine
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_card_at = 0.0
        self._loud: set = set()         # alertas cujo próximo cartão deve tocar (lembrete)
        self.last_error: Optional[str] = None

    def start(self) -> None:
        global PANEL
        PANEL = self
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="painel-seguranca")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_update(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            woke = self._wake.wait(self.REFRESH_SEC)
            if self._stop.is_set():
                return
            if woke:
                self._stop.wait(self.DEBOUNCE_SEC)       # junta várias mudanças seguidas numa edição só
            self._wake.clear()
            try:
                self.sync_cards()
                self.update(only_if_pending=not woke)
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Painel de segurança no Telegram: {e}")

    # ------------------------------------------------------------ cartões de cada alerta
    def _set_cards(self, event_id: int, cards: Dict[str, int]) -> None:
        with get_db_connection() as conn:
            conn.execute("UPDATE security_events SET tg_msgs = ? WHERE id = ?",
                         (json.dumps(cards) if cards else "", int(event_id)))
            conn.commit()

    def drop_cards(self, ev: Dict[str, Any], notify_again: bool = False) -> int:
        """Apaga os cartões de um alerta no Telegram (decidido, ou para reenviar como lembrete)."""
        cards = _cards(ev)
        call = sec_notifier(self.engine)._call
        for chat, msg_id in cards.items():
            call("deleteMessage", {"chat_id": chat, "message_id": msg_id})
        self._set_cards(ev["id"], {})
        if notify_again:
            self._loud.add(int(ev["id"]))
        return len(cards)

    def sync_cards(self, now: Optional[datetime] = None) -> Dict[str, int]:
        """
        Deixa o grupo igual ao banco: alerta pendente de hoje sem cartão ganha um;
        alerta já decidido (painel, Telegram ou descarte automático) perde o seu.
        """
        out = {"enviados": 0, "apagados": 0}
        if not sec_notifier(self.engine).enabled:
            return out
        people = {r["chat"]: r for r in recipients(self.engine)}
        now = now or datetime.now()
        call = sec_notifier(self.engine)._call
        with self._lock:
            with get_db_connection() as conn:
                done = [dict(r) for r in conn.execute(
                    "SELECT id, tg_msgs FROM security_events WHERE status != 'PENDING' AND tg_msgs != ''")]
            for ev in done:
                out["apagados"] += self.drop_cards(ev)
            pending = pending_today(now)[-self.MAX_CARDS:]
            for ev in pending:
                cards = _cards(ev)
                # quem deixou de receber (desativado, sem permissão, desligou ou desvinculou): o cartão some
                gone = [c for c in cards if c not in people]
                for chat in gone:
                    call("deleteMessage", {"chat_id": chat, "message_id": cards.pop(chat)})
                    out["apagados"] += 1
                if gone:
                    self._set_cards(ev["id"], cards)
                missing = [c for c in people if c not in cards]
                if not missing:
                    continue
                loud = int(ev["id"]) in self._loud or time.time() - self._last_card_at > self.QUIET_SEC
                text = security_card_message(ev, ev.get("camera") or self.engine.settings.camera_name, now)
                for chat in missing:
                    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_notification": not loud}
                    kb = card_keyboard(ev["id"], "c", people[chat]["decide"])
                    if kb:
                        payload["reply_markup"] = kb
                    ok, res = call("sendMessage", payload)
                    if ok and isinstance(res, dict) and res.get("message_id"):
                        cards[chat] = res["message_id"]
                        out["enviados"] += 1
                    else:
                        self.last_error = str(res)
                if cards:
                    self._set_cards(ev["id"], cards)
                    self._loud.discard(int(ev["id"]))
                    if loud:
                        self._last_card_at = time.time()
        return out

    # ------------------------------------------------------------ lista fixada
    def _msgs(self) -> Dict[str, Any]:
        from database import system_settings
        try:
            return json.loads(system_settings.get_all().get(PANEL_MSGS_KEY) or "{}")
        except ValueError:
            return {}

    def _save(self, msgs: Dict[str, Any]) -> None:
        from database import system_settings
        system_settings.set_value(PANEL_MSGS_KEY, json.dumps(msgs))

    def update(self, only_if_pending: bool = False, now: Optional[datetime] = None) -> int:
        """Edita (ou cria e fixa) o painel em cada chat de segurança. Devolve quantos chats foram atualizados."""
        people = {r["chat"]: r for r in recipients(self.engine)}
        msgs, n = self._msgs(), 0
        call = sec_notifier(self.engine)._call
        # quem deixou de receber perde também a lista fixada (nada de alerta antigo no celular de ex-funcionário)
        for chat in [c for c in msgs if c not in people]:
            if (msgs[chat] or {}).get("id"):
                call("deleteMessage", {"chat_id": chat, "message_id": msgs[chat]["id"]})
            msgs.pop(chat)
            self._save(msgs)
        if not people or not panel_enabled():
            return 0
        now = now or datetime.now()
        pending = pending_today(now)
        if only_if_pending and not pending:
            return 0
        text = panel_text(now)
        today = now.strftime("%Y-%m-%d")
        targets = list(people)
        for chat in targets:
            keyboard = pending_keyboard(pending, people[chat]["decide"])
            cur = msgs.get(chat) or {}
            if cur.get("id") and cur.get("dia") == today:
                payload = {"chat_id": chat, "message_id": cur["id"], "text": text,
                           "parse_mode": "HTML", "disable_web_page_preview": True}
                if keyboard:
                    payload["reply_markup"] = keyboard
                ok, res = call("editMessageText", payload)
                if ok or "not modified" in str(res):
                    n += 1
                    continue
            # novo dia ou mensagem apagada: cria outra e fixa no topo (sem notificar)
            payload = {"chat_id": chat, "text": text, "parse_mode": "HTML",
                       "disable_notification": True, "disable_web_page_preview": True}
            if keyboard:
                payload["reply_markup"] = keyboard
            ok, res = call("sendMessage", payload)
            if ok and isinstance(res, dict) and res.get("message_id"):
                old = cur.get("id")
                msgs[chat] = {"id": res["message_id"], "dia": today}
                call("pinChatMessage", {"chat_id": chat, "message_id": res["message_id"], "disable_notification": True})
                if old:
                    call("unpinChatMessage", {"chat_id": chat, "message_id": old})
                n += 1
            else:
                self.last_error = str(res)
        self._save({k: v for k, v in msgs.items() if k in targets})
        return n

    def is_panel_message(self, chat_id: str, message_id: int) -> bool:
        return (self._msgs().get(str(chat_id)) or {}).get("id") == message_id
