"""
Bot dos responsáveis: menu com botões (sem digitar comandos).

    [🕒 Presenças recentes] [📊 Frequência]
    [🚗 Retiradas]          [👥 Responsáveis]
    [📅 Dias sem aula]      [🔔 O que receber]

* Presenças recentes / Frequência: tiradas da frequência consolidada da escola
  (dias letivos, feriados, faltas justificadas) — não de "dias com câmera".
* Retiradas: autorizar quem busca o aluno num dia específico. Pedido do
  responsável PRINCIPAL vale na hora; de outro responsável, o principal recebe um
  cartão com a foto e [Aprovar] [Recusar] (sem resposta, expira no fim do dia).
* Responsáveis: todos veem a lista; só o principal adiciona (nome, parentesco,
  celular, foto opcional -> convite para encaminhar) ou remove. Trocar o principal
  é só pela escola.
* O que receber: cada responsável liga/desliga entrada, saída e ausência.

Toda ação confere de novo, no banco, se este Telegram é responsável pelo aluno.
As conversas (passo a passo) ficam em memória e expiram em 15 minutos.
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional

from database import attendance, guardians as gdb, pickups, school
from database.access import log_audit

logger = logging.getLogger(__name__)

SESSION_SEC = 15 * 60
WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
PICK_RELS = ["Avó", "Avô", "Tia", "Tio", "Mãe", "Pai", "Irmã", "Irmão", "Padrinho", "Madrinha", "Vizinho(a)",
             "Transporte escolar"]
GUARD_RELS = ["Mãe", "Pai", "Avó", "Avô", "Tia", "Tio", "Irmã", "Irmão", "Madrasta", "Padrasto", "Responsável legal"]
NOTICES = [("entrada", "Entrada na escola"), ("saida", "Saída da escola"), ("ausencia", "Ausência (não chegou)")]
COMMANDS = [{"command": "menu", "description": "Abrir o menu"}, {"command": "cancelar", "description": "Cancelar"}]

NEEDS_STUDENT = {"p": "🕒 Presenças recentes", "f": "📊 Frequência", "r": "🚗 Retiradas", "g": "👥 Responsáveis"}


def _btn(text: str, data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": data[:64]}


def _kb(*rows) -> Dict[str, Any]:
    return {"inline_keyboard": [list(r) for r in rows if r]}


def _ddmm(d: Any) -> str:
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return f"{d:%d/%m} ({WEEKDAYS[d.weekday()]})"


def _day_label(d: Any) -> str:
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    today = date.today()
    if d == today:
        return f"hoje, {d:%d/%m}"
    if d == today + timedelta(days=1):
        return f"amanhã, {d:%d/%m}"
    return _ddmm(d)


def _first(name: str) -> str:
    return (name or "").split(" ")[0]


MENU_KB = _kb([_btn("🕒 Presenças recentes", "gb:p"), _btn("📊 Frequência", "gb:f")],
              [_btn("🚗 Retiradas", "gb:r"), _btn("👥 Responsáveis", "gb:g")],
              [_btn("📅 Dias sem aula", "gb:c"), _btn("🔔 O que receber", "gb:n")])
BACK = [_btn("⬅️ Menu", "gb:m")]


class GuardianBot:
    def __init__(self, engine):
        self.engine = engine
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ Telegram
    @property
    def notifier(self):
        return self.engine.notifier

    def _call(self, method: str, payload: Dict[str, Any]):
        return self.notifier._call(method, payload)

    def _send(self, chat: str, text: str, kb: Optional[Dict[str, Any]] = None) -> Optional[int]:
        payload = {"chat_id": str(chat), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if kb:
            payload["reply_markup"] = kb
        ok, res = self._call("sendMessage", payload)
        return res.get("message_id") if ok and isinstance(res, dict) else None

    def _edit(self, msg: Dict[str, Any], text: str, kb: Optional[Dict[str, Any]] = None) -> None:
        """Troca a tela na MESMA mensagem (o chat não vira uma pilha de menus)."""
        chat = (msg.get("chat") or {}).get("id")
        if chat is None or not msg.get("message_id"):
            return
        if msg.get("photo"):          # mensagem com foto: não dá para virar texto; manda uma nova
            self._send(str(chat), text, kb)
            return
        payload = {"chat_id": chat, "message_id": msg["message_id"], "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True, "reply_markup": kb or {"inline_keyboard": []}}
        ok, res = self._call("editMessageText", payload)
        if not ok and "not modified" not in str(res):
            self._send(str(chat), text, kb)

    def _answer(self, cq: Dict[str, Any], text: str = "", alert: bool = False) -> None:
        payload = {"callback_query_id": cq.get("id", "")}
        if text:
            payload.update(text=text[:190], show_alert=bool(alert))
        self._call("answerCallbackQuery", payload)

    def register_commands(self, notifier=None) -> bool:
        ok, _ = self._call("setMyCommands", {"commands": COMMANDS, "scope": {"type": "all_private_chats"}})
        return bool(ok)

    # ------------------------------------------------------------ quem é quem
    @staticmethod
    def _links(chat: str) -> List[Dict[str, Any]]:
        return gdb.links_for_chat(chat)

    @staticmethod
    def _students(links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: Dict[int, Dict[str, Any]] = {}
        for l in links:
            cur = seen.get(l["student_id"])
            if cur is None or (l["is_primary"] and not cur["is_primary"]):
                seen[l["student_id"]] = l
        return list(seen.values())

    def _link(self, chat: str, sid: int) -> Optional[Dict[str, Any]]:
        return next((l for l in self._students(self._links(chat)) if l["student_id"] == int(sid)), None)

    # ------------------------------------------------------------ menu
    def menu_text(self, chat: str) -> str:
        studs = self._students(self._links(chat))
        names = ", ".join(escape(_first(s["student_name"])) for s in studs)
        return (f"🏫 <b>RealGuard · {escape(self._school())}</b>\n"
                f"Aluno(s): <b>{names}</b>\n\nO que você quer ver?")

    def _school(self) -> str:
        try:
            return school.get_settings().get("school_name") or "Escola"
        except Exception:
            return "Escola"

    def send_menu(self, chat: str, prefix: str = "") -> None:
        if not self._links(chat):
            return
        self._send(chat, (prefix + "\n\n" if prefix else "") + self.menu_text(chat), MENU_KB)

    def _pick_student(self, msg, chat: str, act: str) -> None:
        studs = self._students(self._links(chat))
        rows = [[_btn(f"{_first(s['student_name'])}" + (f" · {s['class_name']}" if s.get("class_name") else ""),
                      f"gb:{act}:{s['student_id']}")] for s in studs]
        self._edit(msg, f"{NEEDS_STUDENT[act]}\n\nDe qual aluno?", _kb(*rows, BACK))

    # ------------------------------------------------------------ mensagens (texto / foto)
    def handle_message(self, msg: Dict[str, Any]) -> Optional[str]:
        chat_d = msg.get("chat") or {}
        if chat_d.get("type") != "private" or "id" not in chat_d or msg.get("contact"):
            return None
        chat = str(chat_d["id"])
        text = (msg.get("text") or msg.get("caption") or "").strip()
        photos = msg.get("photo") or []
        cmd = text.split(maxsplit=1)[0].split("@")[0].lower() if text.startswith("/") else ""
        if cmd == "/start" and len(text.split()) > 1:
            return None                         # convite: vínculo normal (telegram_link)
        links = self._links(chat)
        if not links:
            return None                         # ainda não vinculado: o fluxo de vínculo cuida
        if cmd in ("/start", "/menu"):
            self._drop(chat)
            self.send_menu(chat)
            return "menu"
        if cmd == "/cancelar":
            self._drop(chat)
            self.send_menu(chat, "Tudo bem, cancelei.")
            return "cancelled"
        s = self._session(chat)
        if s is None:
            if text or photos:
                self.send_menu(chat, "Use os botões abaixo 👇")
                return "menu"
            return None
        return self._step(chat, s, text, photos)

    # ------------------------------------------------------------ conversas
    def _session(self, chat: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            s = self.sessions.get(chat)
            if s and time.monotonic() - s["at"] > SESSION_SEC:
                self.sessions.pop(chat, None)
                s = None
            return s

    def _start(self, chat: str, flow: str, sid: int, step: str) -> Dict[str, Any]:
        with self._lock:
            s = {"flow": flow, "sid": int(sid), "step": step, "at": time.monotonic()}
            self.sessions[chat] = s
            return s

    def _drop(self, chat: str) -> None:
        with self._lock:
            self.sessions.pop(chat, None)

    CANCEL_ROW = [_btn("✖️ Cancelar", "gb:x")]

    def _ask(self, chat: str, s: Dict[str, Any], step: str, text: str, kb: Optional[Dict[str, Any]] = None) -> str:
        s["step"], s["at"] = step, time.monotonic()
        self._send(chat, text, kb or _kb(self.CANCEL_ROW))
        return "ask_" + step

    def _step(self, chat: str, s: Dict[str, Any], text: str, photos: list) -> str:
        step = s["step"]
        s["at"] = time.monotonic()
        if photos and not step.endswith("photo"):
            self._send(chat, "Agora não preciso de foto. Responda à pergunta acima (ou toque em ✖️ Cancelar).")
            return "unexpected_photo"
        if step == "date_text":
            d = self._parse_date(text)
            if d is None:
                self._send(chat, "Não entendi a data. Envie no formato <b>DD/MM</b> (ex.: 15/10).",
                           _kb(self.CANCEL_ROW))
                return "bad_date"
            s["day"] = d
            return self._ask(chat, s, "name", "Qual o <b>nome completo</b> de quem vai buscar?")
        if step in ("name", "g_name"):
            name = " ".join(text.split())
            if not 3 <= len(name) <= 80 or name.startswith("/"):
                self._send(chat, "Envie o nome completo (de 3 a 80 letras).", _kb(self.CANCEL_ROW))
                return "bad_name"
            s["name"] = name
            if step == "name":
                return self._ask(chat, s, "rel", f"Qual a relação de <b>{escape(name)}</b> com o aluno?",
                                 self._rel_kb(PICK_RELS, "rr"))
            return self._ask(chat, s, "g_rel", f"Qual o parentesco de <b>{escape(name)}</b> com o aluno?",
                             self._rel_kb(GUARD_RELS, "gl"))
        if step in ("rel_text", "g_rel_text"):
            rel = " ".join(text.split())
            if not 2 <= len(rel) <= 40:
                self._send(chat, "Escreva a relação em poucas palavras (ex.: Tio, Vizinha).", _kb(self.CANCEL_ROW))
                return "bad_rel"
            s["rel"] = rel
            return self._after_rel(chat, s)
        if step == "g_phone":
            try:
                phone = gdb.clean_phone(text)
            except gdb.GuardianError as e:
                self._send(chat, escape(str(e)), _kb(self.CANCEL_ROW))
                return "bad_phone"
            if not phone:
                self._send(chat, "Envie o celular com DDD.", _kb(self.CANCEL_ROW))
                return "bad_phone"
            s["phone"] = phone
            return self._ask_photo(chat, s, "g_photo", "do novo responsável",
                                   "Ela aparece no cadastro do aluno para a portaria reconhecer.")
        if step in ("photo", "g_photo"):
            if not photos:
                self._send(chat, "Envie a foto pelo clipe 📎 → Foto, ou toque em ⏭ Pular.",
                           _kb([_btn("⏭ Pular", "gb:sk")], self.CANCEL_ROW))
                return "need_photo"
            try:
                s["photo"] = self.notifier.download_photo(photos[-1]["file_id"])
            except ValueError as e:
                self._send(chat, escape(str(e)), _kb([_btn("⏭ Pular", "gb:sk")], self.CANCEL_ROW))
                return "photo_error"
            return self._confirm(chat, s)
        self._send(chat, "Use os botões da última mensagem (ou /cancelar).")
        return "waiting_button"

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        t = text.strip().replace("-", "/").replace(".", "/")
        today = date.today()
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m"):
            try:
                d = datetime.strptime(t, fmt).date()
            except ValueError:
                continue
            if fmt == "%d/%m":
                d = d.replace(year=today.year)
                if d < today:
                    d = d.replace(year=today.year + 1)
            return d
        return None

    @staticmethod
    def _rel_kb(options: List[str], prefix: str) -> Dict[str, Any]:
        rows, row = [], []
        for i, r in enumerate(options):
            row.append(_btn(r, f"gb:{prefix}:{i}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([_btn("Outro…", f"gb:{prefix}:o")])
        rows.append(GuardianBot.CANCEL_ROW)
        return {"inline_keyboard": rows}

    def _ask_photo(self, chat: str, s: Dict[str, Any], step: str, whose: str, why: str) -> str:
        return self._ask(chat, s, step, f"📷 Envie uma <b>foto do rosto</b> {whose} (opcional).\n{why}\n\n"
                                        "<i>A foto fica só no sistema da escola.</i>",
                         _kb([_btn("⏭ Pular", "gb:sk")], self.CANCEL_ROW))

    def _after_rel(self, chat: str, s: Dict[str, Any]) -> str:
        if s["flow"] == "retirada":
            return self._ask_photo(chat, s, "photo", "de quem vai buscar",
                                   "Ajuda a portaria a conferir. Ela é apagada 7 dias depois da data.")
        return self._ask(chat, s, "g_phone", f"Qual o <b>celular</b> de {escape(_first(s['name']))} (com DDD)?\n"
                                             "<i>É por ele que a pessoa ativa os avisos no Telegram.</i>")

    def _confirm(self, chat: str, s: Dict[str, Any]) -> str:
        link = self._link(chat, s["sid"])
        if link is None:
            self._drop(chat)
            self._send(chat, "Este aluno não está mais ligado ao seu Telegram.")
            return "not_linked"
        foto = "anexada ✅" if s.get("photo") else "sem foto"
        if s["flow"] == "retirada":
            lines = ["🚗 <b>Confira a autorização de retirada</b>", "",
                     f"Aluno: <b>{escape(link['student_name'])}</b>",
                     f"Quem busca: <b>{escape(s['name'])}</b> ({escape(s['rel'])})",
                     f"Dia: <b>{_day_label(s['day'])}</b>", f"Foto: {foto}"]
            if not link["is_primary"]:
                prim = ", ".join(_first(p["name"]) for p in gdb.for_student(s["sid"]) if p.get("is_primary"))
                lines += ["", f"⏳ Como você não é o responsável principal, <b>{escape(prim or 'o principal')}</b> "
                              "precisa aprovar. Sem resposta até o fim do dia, o pedido expira."]
        else:
            lines = ["👥 <b>Confira o novo responsável</b>", "",
                     f"Aluno: <b>{escape(link['student_name'])}</b>",
                     f"Nome: <b>{escape(s['name'])}</b> ({escape(s['rel'])})",
                     f"Celular: {escape(s['phone'])}", f"Foto: {foto}", "",
                     "Ele(a) passa a receber os avisos depois de abrir o convite que eu vou te mandar."]
        return self._ask(chat, s, "confirm", "\n".join(lines),
                         _kb([_btn("✅ Confirmar", "gb:ok"), _btn("✖️ Cancelar", "gb:x")]))

    # ------------------------------------------------------------ botões
    def handle_callback(self, cq: Dict[str, Any]) -> str:
        msg = cq.get("message") or {}
        chat = str((cq.get("from") or {}).get("id") or (msg.get("chat") or {}).get("id") or "")
        parts = str(cq.get("data") or "").split(":")
        act, args = (parts[1] if len(parts) > 1 else ""), parts[2:]
        links = self._links(chat)
        if not links:
            self._answer(cq, "Seu Telegram não está ligado a nenhum aluno. Abra o convite da escola.", alert=True)
            return "not_linked"
        try:
            out = self._dispatch(cq, msg, chat, act, args)
        except pickups.PickupError as e:
            self._answer(cq, str(e), alert=True)
            return "error"
        except (ValueError, IndexError):
            self._answer(cq)
            return "ignored"
        return out

    def _dispatch(self, cq, msg, chat: str, act: str, args: List[str]) -> str:
        if act == "m":
            self._drop(chat)
            self._edit(msg, self.menu_text(chat), MENU_KB)
            self._answer(cq)
            return "menu"
        if act == "x":
            self._drop(chat)
            self._answer(cq, "Cancelado.")
            self._edit(msg, "Cancelado. " + self.menu_text(chat), MENU_KB)
            return "cancelled"
        if act in NEEDS_STUDENT:
            if not args:
                studs = self._students(self._links(chat))
                if len(studs) > 1:
                    self._pick_student(msg, chat, act)
                    self._answer(cq)
                    return "pick_student"
                args = [str(studs[0]["student_id"])]
            sid = int(args[0])
            link = self._link(chat, sid)
            if link is None:
                self._answer(cq, "Este aluno não está mais ligado ao seu Telegram.", alert=True)
                return "not_linked"
            screen = {"p": self.recent_text, "f": self.frequency_text}.get(act)
            if screen:
                self._edit(msg, screen(sid, link), _kb(BACK))
            elif act == "r":
                text, kb = self.pickups_screen(chat, link)
                self._edit(msg, text, kb)
            else:
                text, kb = self.guardians_screen(link)
                self._edit(msg, text, kb)
            self._answer(cq)
            return "screen_" + act
        if act == "c":
            self._edit(msg, self.calendar_text(), _kb(BACK))
            self._answer(cq)
            return "screen_c"
        if act in ("n", "nt"):
            if act == "nt" and args and args[0] in gdb.NOTICE_COLUMNS:
                cur = gdb.notice_prefs(chat)
                gdb.set_notice_pref(chat, args[0], not cur[args[0]])
            text, kb = self.prefs_screen(chat)
            self._edit(msg, text, kb)
            self._answer(cq, "Preferência salva." if act == "nt" else "")
            return "prefs"
        # ---- retirada
        if act == "ra":
            link = self._require(cq, chat, args[0])
            if not link:
                return "not_linked"
            if not link["is_primary"] and not pickups.primary_chats(link["student_id"]):
                self._answer(cq, "O responsável principal ainda não usa este bot, então não há quem aprove. "
                                 "Peça para ele autorizar ou fale com a escola.", alert=True)
                return "no_primary"
            s = self._start(chat, "retirada", link["student_id"], "date")
            self._answer(cq)
            today = date.today()
            return self._ask(chat, s, "date", f"🚗 Retirada de <b>{escape(link['student_name'])}</b>\n\nPara qual dia?",
                             _kb([_btn(f"Hoje ({today:%d/%m})", "gb:rd:0"),
                                  _btn(f"Amanhã ({today + timedelta(days=1):%d/%m})", "gb:rd:1")],
                                 [_btn("📅 Outra data", "gb:rd:x")], self.CANCEL_ROW))
        if act == "rd":
            s = self._need(cq, chat, "date")
            if not s:
                return "expired"
            self._answer(cq)
            if args[0] == "x":
                return self._ask(chat, s, "date_text", "Envie a data no formato <b>DD/MM</b> (ex.: 15/10).")
            s["day"] = date.today() + timedelta(days=int(args[0]))
            return self._ask(chat, s, "name", "Qual o <b>nome completo</b> de quem vai buscar?")
        if act in ("rr", "gl"):
            s = self._need(cq, chat, "rel" if act == "rr" else "g_rel")
            if not s:
                return "expired"
            self._answer(cq)
            if args[0] == "o":
                return self._ask(chat, s, "rel_text" if act == "rr" else "g_rel_text",
                                 "Escreva a relação com o aluno (ex.: Vizinha, Babá).")
            s["rel"] = (PICK_RELS if act == "rr" else GUARD_RELS)[int(args[0])]
            return self._after_rel(chat, s)
        if act == "sk":
            s = self._session(chat)
            if not s or s["step"] not in ("photo", "g_photo"):
                self._answer(cq, "Essa conversa expirou. Abra o /menu.", alert=True)
                return "expired"
            self._answer(cq)
            s["photo"] = None
            return self._confirm(chat, s)
        if act == "ok":
            s = self._need(cq, chat, "confirm")
            if not s:
                return "expired"
            self._answer(cq)
            self._edit(msg, (msg.get("text") or "") + "\n\n⏳ Registrando…", None)
            return self._finish_pickup(chat, s) if s["flow"] == "retirada" else self._finish_guardian(chat, s)
        if act in ("ap", "rj"):
            return self._decide_pickup(cq, msg, chat, int(args[0]), act == "ap")
        if act == "rx":
            p = pickups.cancel(int(args[0]), "responsável: " + self._name(chat, args[0]), chat_id=chat)
            self._answer(cq, "Autorização cancelada.")
            link = self._link(chat, p["student_id"])
            text, kb = self.pickups_screen(chat, link)
            self._edit(msg, text, kb)
            self._notify_cancel(chat, p)
            log_audit("retirada_cancelada", target_type="aluno", target_id=p["student_id"], username="telegram",
                      detail=f"{p['person_name']} em {p['day']} (pelo responsável)")
            return "pickup_cancelled"
        # ---- responsáveis
        if act == "ga":
            link = self._require(cq, chat, args[0], primary=True)
            if not link:
                return "not_primary"
            s = self._start(chat, "responsavel", link["student_id"], "g_name")
            self._answer(cq)
            return self._ask(chat, s, "g_name", f"👥 Novo responsável de <b>{escape(link['student_name'])}</b>\n\n"
                                                "Qual o <b>nome completo</b>?")
        if act in ("gr", "gR"):
            link = self._require(cq, chat, args[0], primary=True)
            if not link:
                return "not_primary"
            gid = int(args[1])
            target = next((g for g in gdb.for_student(link["student_id"]) if g["id"] == gid), None)
            if target is None or target.get("is_primary"):
                self._answer(cq, "Este responsável não pode ser removido por aqui.", alert=True)
                return "cant_remove"
            if act == "gr":
                self._answer(cq)
                self._edit(msg, f"Remover <b>{escape(target['name'])}</b> ({escape(target.get('relationship') or '')}) "
                                f"dos responsáveis de <b>{escape(link['student_name'])}</b>?\n\n"
                                "Ele(a) deixa de receber os avisos e não poderá mais autorizar retiradas.",
                           _kb([_btn("🗑 Sim, remover", f"gb:gR:{link['student_id']}:{gid}"),
                                _btn("Voltar", f"gb:g:{link['student_id']}")]))
                return "confirm_remove"
            gdb.unlink(link["student_id"], gid)
            self._refresh()
            log_audit("responsavel_removido", target_type="aluno", target_id=link["student_id"], username="telegram",
                      detail=f"{target['name']} removido por {link['guardian_name']} (responsável principal, Telegram)")
            if target.get("telegram_chat_id") and str(target["telegram_chat_id"]) != chat:
                self._send(target["telegram_chat_id"],
                           f"ℹ️ Você não é mais responsável por <b>{escape(link['student_name'])}</b> no RealGuard "
                           f"(alterado por {escape(_first(link['guardian_name']))}). Os avisos deste aluno param aqui.")
            self._answer(cq, "Responsável removido.")
            text, kb = self.guardians_screen(self._link(chat, link["student_id"]))
            self._edit(msg, text, kb)
            return "guardian_removed"
        self._answer(cq)
        return "ignored"

    def _require(self, cq, chat: str, sid: str, primary: bool = False) -> Optional[Dict[str, Any]]:
        link = self._link(chat, int(sid))
        if link is None:
            self._answer(cq, "Este aluno não está mais ligado ao seu Telegram.", alert=True)
            return None
        if primary and not link["is_primary"]:
            self._answer(cq, "Só o responsável principal pode fazer isso. Para mudar o principal, fale com a escola.",
                         alert=True)
            return None
        return link

    def _need(self, cq, chat: str, step: str) -> Optional[Dict[str, Any]]:
        s = self._session(chat)
        if not s or s["step"] != step:
            self._answer(cq, "Essa conversa expirou ou já andou. Abra o /menu para recomeçar.", alert=True)
            return None
        return s

    def _name(self, chat: str, _=None) -> str:
        links = self._links(chat)
        return links[0]["guardian_name"] if links else chat

    def _refresh(self) -> None:
        try:
            self.engine.refresh_students()
        except Exception:
            pass

    # ------------------------------------------------------------ concluir
    def _finish_pickup(self, chat: str, s: Dict[str, Any]) -> str:
        self._drop(chat)
        try:
            p = pickups.create(chat, s["sid"], s["name"], s["rel"], s["day"])
        except pickups.PickupError as e:
            self._send(chat, "❌ " + escape(str(e)))
            self.send_menu(chat)
            return "pickup_error"
        if s.get("photo"):
            pickups.save_photo(p["id"], s["photo"])
            p = pickups.get(p["id"])
        log_audit("retirada_autorizada" if p["status"] == pickups.ACTIVE else "retirada_pedida", target_type="aluno",
                  target_id=p["student_id"], username="telegram",
                  detail=f"{p['person_name']} ({p['relationship']}) em {p['day']}, por {p['requested_by_name']}")
        if p["status"] == pickups.ACTIVE:
            self._send(chat, f"✅ <b>Retirada autorizada</b>\n{escape(p['person_name'])} ({escape(p['relationship'])}) "
                             f"pode buscar <b>{escape(p['student_name'])}</b> em <b>{_day_label(p['day'])}</b>.\n"
                             "A portaria vê a autorização na ficha do aluno.", _kb([_btn("🚗 Ver retiradas", f"gb:r:{p['student_id']}")], BACK))
            self._inform_others(chat, p)
            return "pickup_active"
        self._ask_primary(p)
        self._send(chat, f"⏳ <b>Pedido enviado</b> ao responsável principal.\nVocê recebe a resposta aqui. "
                         f"Se não houver resposta até o fim de {_day_label(p['day'])}, o pedido expira.", _kb(BACK))
        return "pickup_pending"

    def _pickup_caption(self, p: Dict[str, Any]) -> str:
        return (f"🚗 <b>Pedido de retirada</b>\n\n<b>{escape(p['requested_by_name'] or 'Um responsável')}</b>"
                f" ({escape(p.get('requested_by_relationship') or 'responsável')}) pediu para autorizar:\n\n"
                f"Quem busca: <b>{escape(p['person_name'])}</b> ({escape(p['relationship'])})\n"
                f"Aluno: <b>{escape(p['student_name'])}</b>\nDia: <b>{_day_label(p['day'])}</b>"
                + ("" if p["tem_foto"] else "\nFoto: não enviada"))

    def _ask_primary(self, p: Dict[str, Any]) -> None:
        kb = _kb([_btn("✅ Aprovar", f"gb:ap:{p['id']}"), _btn("✖️ Recusar", f"gb:rj:{p['id']}")])
        msgs = {}
        for chat in pickups.primary_chats(p["student_id"]):
            if p["tem_foto"]:
                with open(pickups.photo_path(p["id"]), "rb") as f:
                    jpeg = f.read()
                ok, res = self._send_photo(chat, jpeg, self._pickup_caption(p), kb)
            else:
                mid = self._send(chat, self._pickup_caption(p), kb)
                ok, res = bool(mid), {"message_id": mid}
            if ok and isinstance(res, dict) and res.get("message_id"):
                msgs[chat] = res["message_id"]
        pickups.set_approval_msgs(p["id"], msgs)

    def _send_photo(self, chat: str, jpeg: bytes, caption: str, kb: Optional[Dict[str, Any]]):
        """sendPhoto devolvendo o message_id (para editar o cartão depois)."""
        import json as _json
        import uuid
        b = "----rg" + uuid.uuid4().hex

        def field(n, v):
            return f'--{b}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode("utf-8")
        body = (field("chat_id", chat) + field("caption", caption[:1000]) + field("parse_mode", "HTML")
                + (field("reply_markup", _json.dumps(kb)) if kb else b"")
                + f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="foto.jpg"\r\n'
                  f'Content-Type: image/jpeg\r\n\r\n'.encode() + jpeg + f"\r\n--{b}--\r\n".encode())
        return self.notifier._call("sendPhoto", raw=(body, f"multipart/form-data; boundary={b}"))

    def _decide_pickup(self, cq, msg, chat: str, pid: int, approve: bool) -> str:
        p = pickups.decide(pid, chat, approve)
        self._answer(cq, "Aprovado." if approve else "Recusado.")
        who = _first(p.get("decided_by_name") or "")
        verdict = (f"\n\n✅ <b>Aprovado</b> por {escape(who)}" if approve else f"\n\n✖️ <b>Recusado</b> por {escape(who)}")
        for c, mid in pickups.approval_msgs(p).items():
            if p["tem_foto"]:
                self._call("editMessageCaption", {"chat_id": c, "message_id": mid, "parse_mode": "HTML",
                                                  "caption": self._pickup_caption(p) + verdict,
                                                  "reply_markup": {"inline_keyboard": []}})
            else:
                self._call("editMessageText", {"chat_id": c, "message_id": mid, "parse_mode": "HTML",
                                               "text": self._pickup_caption(p) + verdict,
                                               "reply_markup": {"inline_keyboard": []}})
        if p["requested_chat"] and p["requested_chat"] != chat:
            self._send(p["requested_chat"],
                       (f"✅ <b>{escape(who)} aprovou</b>: {escape(p['person_name'])} pode buscar "
                        f"<b>{escape(p['student_name'])}</b> em <b>{_day_label(p['day'])}</b>.") if approve else
                       (f"✖️ <b>{escape(who)} recusou</b> a retirada de {escape(p['student_name'])} por "
                        f"{escape(p['person_name'])} em {_day_label(p['day'])}."))
        log_audit("retirada_aprovada" if approve else "retirada_recusada", target_type="aluno",
                  target_id=p["student_id"], username="telegram",
                  detail=f"{p['person_name']} em {p['day']}, por {p.get('decided_by_name')} (responsável principal)")
        return "pickup_approved" if approve else "pickup_rejected"

    def _inform_others(self, chat: str, p: Dict[str, Any]) -> None:
        """Retirada autorizada pelo principal: os outros responsáveis ficam sabendo (sem botões)."""
        for c in gdb.chat_ids_for_student(p["student_id"]):
            if c != chat:
                self._send(c, f"ℹ️ {escape(_first(p['requested_by_name'] or ''))} autorizou "
                              f"<b>{escape(p['person_name'])}</b> ({escape(p['relationship'])}) a buscar "
                              f"{escape(p['student_name'])} em {_day_label(p['day'])}.")

    def _notify_cancel(self, chat: str, p: Dict[str, Any]) -> None:
        for c in set(gdb.chat_ids_for_student(p["student_id"])) - {chat}:
            self._send(c, f"ℹ️ A retirada de {escape(p['student_name'])} por {escape(p['person_name'])} em "
                          f"{_day_label(p['day'])} foi <b>cancelada</b>.")
        for c, mid in pickups.approval_msgs(p).items():
            self._call("editMessageReplyMarkup", {"chat_id": c, "message_id": mid,
                                                  "reply_markup": {"inline_keyboard": []}})

    def notify_school_cancel(self, p: Dict[str, Any]) -> None:
        """Cancelada pela escola: todos os responsáveis do aluno ficam sabendo."""
        if not self.notifier.enabled:
            return
        for c in gdb.chat_ids_for_student(p["student_id"]):
            self._send(c, f"ℹ️ A escola <b>cancelou</b> a retirada de {escape(p['student_name'])} por "
                          f"{escape(p['person_name'])} em {_day_label(p['day'])}. Dúvidas? Fale com a secretaria.")
        for c, mid in pickups.approval_msgs(p).items():
            self._call("editMessageReplyMarkup", {"chat_id": c, "message_id": mid,
                                                  "reply_markup": {"inline_keyboard": []}})

    def _finish_guardian(self, chat: str, s: Dict[str, Any]) -> str:
        self._drop(chat)
        link = self._link(chat, s["sid"])
        if link is None or not link["is_primary"]:
            self._send(chat, "Só o responsável principal pode adicionar responsáveis.")
            return "not_primary"
        existing = [g for g in gdb.find_by_phone(s["phone"]) if g["id"] not in
                    [x["id"] for x in gdb.for_student(s["sid"])]]
        if any(gdb.phones_match(g.get("phone", ""), s["phone"]) for g in gdb.for_student(s["sid"])):
            self._send(chat, "Este celular já é de um responsável deste aluno.", _kb(BACK))
            return "duplicate"
        gid = existing[0]["id"] if existing else gdb.create(s["name"], s["phone"])
        gdb.link(s["sid"], gid, s["rel"], primary=False)
        if s.get("photo"):
            gdb.save_photo(gid, s["photo"])
        token = gdb.ensure_link_token(gid)
        self._refresh()
        log_audit("responsavel_adicionado", target_type="aluno", target_id=s["sid"], username="telegram",
                  detail=f"{s['name']} ({s['rel']}) por {link['guardian_name']} (responsável principal, Telegram)")
        bot = self.notifier.get_bot_username()
        invite = f"https://t.me/{bot}?start={token}" if bot and token else ""
        self._send(chat, f"✅ <b>{escape(s['name'])}</b> agora é responsável de <b>{escape(link['student_name'])}</b> "
                         f"({escape(s['rel'])}).\n\n"
                         + (f"Encaminhe este convite para {escape(_first(s['name']))} ativar os avisos:\n{invite}"
                            if invite else "Ele(a) pode ativar os avisos abrindo este bot e compartilhando o número."),
                   _kb([_btn("👥 Ver responsáveis", f"gb:g:{s['sid']}")], BACK))
        return "guardian_added"

    # ------------------------------------------------------------ telas
    def recent_text(self, sid: int, link: Dict[str, Any]) -> str:
        hist = attendance.student_history(sid, 14)
        rows = [r for r in hist["dias"]][:7]
        lines = [f"🕒 <b>Presenças recentes · {escape(link['student_name'])}</b>", ""]
        icons = {"PRESENTE": "✅ Presente", "ATRASADO": "⏰ Atrasado", "AUSENTE": "❌ Falta",
                 "JUSTIFICADA": "📝 Falta justificada", "PENDENTE": "⏳ Ainda não chegou"}
        for r in rows:
            entry = r.get("manual_entry") or r.get("cam_entry")
            extra = " · ".join(x for x in [f"entrou {entry}" if entry else "",
                                           f"saiu {r['cam_exit']}" if r.get("cam_exit") else ""] if x)
            lines.append(f"<b>{_ddmm(r['day'])}</b> {icons.get(r['status'], r['status'])}" + (f" · {extra}" if extra else ""))
        if not rows:
            lines.append("Ainda não há dias letivos registrados.")
        lines += ["", "<i>Só dias letivos. Feriados e recessos não aparecem.</i>"]
        return "\n".join(lines)

    def frequency_text(self, sid: int, link: Dict[str, Any]) -> str:
        today = date.today()
        try:
            cur = next((p for p in school.academic_periods(today)["periodos"] if p["atual"]), None)
        except Exception:
            cur = None
        start = date.fromisoformat(cur["inicio"]) if cur else today - timedelta(days=29)
        hist = attendance.student_history(sid, (today - start).days + 1)
        r = hist["resumo"]
        label = cur["nome"] if cur else "últimos 30 dias"
        lines = [f"📊 <b>Frequência · {escape(link['student_name'])}</b>", f"<i>{escape(label)}</i>", ""]
        if not r["dias_letivos"]:
            return "\n".join(lines + ["Ainda não há dias letivos encerrados neste período."])
        pct = r["frequencia"]
        mark = "🟢" if pct >= 80 else "🟡" if pct >= 75 else "🔴"
        lines += [f"{mark} <b>{pct:.0f}%</b> de presença ({r['presencas']} de {r['dias_letivos']} dias letivos)",
                  f"❌ Faltas: <b>{r['faltas']}</b>" + (f" · 📝 justificadas: {r['justificadas']}" if r["justificadas"] else ""),
                  f"⏰ Atrasos: {r['atrasos']}"]
        faltas = [d["day"] for d in hist["dias"] if d["status"] == "AUSENTE"]
        if faltas:
            lines += ["", "Dias de falta: " + ", ".join(_ddmm(d) for d in faltas[:10])
                      + (f" e mais {len(faltas) - 10}" if len(faltas) > 10 else "")]
        if pct < 75:
            lines += ["", "⚠️ Abaixo do mínimo de 75%. Se houver motivo (atestado), fale com a escola."]
        return "\n".join(lines)

    def calendar_text(self) -> str:
        today = date.today()
        limit = today + timedelta(days=90)
        entries = []
        for year in sorted({today.year, limit.year}):
            entries += [e for e in school.list_calendar(year) if e["kind"] in school.NO_SCHOOL_KINDS
                        and e["end_date"] >= today.isoformat() and e["start_date"] <= limit.isoformat()]
        seen, lines = set(), ["📅 <b>Próximos dias sem aula</b>", "<i>próximos 90 dias</i>", ""]
        for e in entries:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            a, b = date.fromisoformat(e["start_date"]), date.fromisoformat(e["end_date"])
            when = _ddmm(a) if a == b else f"{a:%d/%m} a {b:%d/%m}"
            turno = f" · só {escape(e['shift_name'])}" if e.get("shift_name") else ""
            lines.append(f"• <b>{when}</b> — {escape(e['description'])}{turno}")
        if len(lines) == 3:
            lines.append("Nenhum feriado ou recesso cadastrado nos próximos 90 dias.")
        return "\n".join(lines)

    def prefs_screen(self, chat: str):
        prefs = gdb.notice_prefs(chat)
        lines = ["🔔 <b>O que você recebe</b>", "Toque para ligar ou desligar.", ""]
        rows = []
        for key, label in NOTICES:
            on = prefs[key]
            lines.append(f"{'✅' if on else '⬜'} {label}")
            rows.append([_btn(f"{'✅' if on else '⬜'} {label}", f"gb:nt:{key}")])
        lines += ["", "<i>Vale para todos os seus alunos. Pedidos de retirada e avisos da escola chegam sempre.</i>"]
        return "\n".join(lines), _kb(*rows, BACK)

    def pickups_screen(self, chat: str, link: Dict[str, Any]):
        sid = link["student_id"]
        rows = pickups.for_chat(chat, sid)
        lines = [f"🚗 <b>Retiradas · {escape(link['student_name'])}</b>", ""]
        kb_rows = []
        for p in rows:
            icon = "✅" if p["situacao"] == pickups.ACTIVE else "⏳"
            lines.append(f"{icon} <b>{_day_label(p['day'])}</b> · {escape(p['person_name'])} ({escape(p['relationship'])})"
                         + ("" if p["situacao"] == pickups.ACTIVE else " — aguardando o principal"))
            if link["is_primary"] or str(p["requested_chat"]) == chat:
                kb_rows.append([_btn(f"✖️ Cancelar {_first(p['person_name'])} · {p['day'][8:10]}/{p['day'][5:7]}",
                                     f"gb:rx:{p['id']}")])
        if not rows:
            lines.append("Nenhuma retirada autorizada para os próximos dias.")
        lines += ["", "Autorize aqui quando <b>outra pessoa</b> for buscar o aluno num dia específico."
                      + ("" if link["is_primary"] else " Como você não é o principal, ele(a) precisa aprovar.")]
        return "\n".join(lines), _kb([_btn("➕ Autorizar retirada", f"gb:ra:{sid}")], *kb_rows, BACK)

    def guardians_screen(self, link: Dict[str, Any]):
        sid = link["student_id"]
        gs = gdb.for_student(sid)
        lines = [f"👥 <b>Responsáveis · {escape(link['student_name'])}</b>", ""]
        kb_rows = []
        for g in gs:
            star = "⭐ " if g.get("is_primary") else "• "
            tg = "📱" if g.get("telegram_vinculado") else "—"
            lines.append(f"{star}<b>{escape(g['name'])}</b> ({escape(g.get('relationship') or 'Responsável')}) {tg}")
            if link["is_primary"] and not g.get("is_primary"):
                kb_rows.append([_btn(f"🗑 Remover {_first(g['name'])}", f"gb:gr:{sid}:{g['id']}")])
        lines += ["", "⭐ principal · 📱 recebe avisos no Telegram"]
        if link["is_primary"]:
            lines.append("<i>Para trocar o responsável principal, fale com a escola.</i>")
            return "\n".join(lines), _kb([_btn("➕ Adicionar responsável", f"gb:ga:{sid}")], *kb_rows, BACK)
        lines.append("<i>Só o responsável principal adiciona ou remove responsáveis.</i>")
        return "\n".join(lines), _kb(BACK)
