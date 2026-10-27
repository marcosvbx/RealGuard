"""Textos das mensagens do Telegram (HTML). Nomes são sempre escapados."""

from config.clock import datetime
from html import escape
from typing import Any, Dict, Optional

from app.school.rm import display_rm


def _minutes(hhmm: str) -> int:
    """'19:35' -> 1175. Valor inválido => fim do dia (nunca marca atraso por engano)."""
    try:
        h, m = (str(hhmm).split(":") + ["0"])[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return 24 * 60


def _who(student: Dict[str, Any]) -> str:
    extra = " · ".join(x for x in [
        escape(student.get("class_name") or ""),
        escape(display_rm(student["registration_number"])) if student.get("registration_number") else "",
    ] if x)
    return f"👤 <b>{escape(student.get('name', ''))}</b>" + (f"\n🎓 {extra}" if extra else "")


def presence_message(student: Dict[str, Any], camera: str, direction: str = "ENTRY",
                     tolerance: str = "", now: Optional[datetime] = None) -> str:
    """Entrada (no horário ou com atraso) e saída — no texto que a escola escolheu (ou no padrão)."""
    from app.notifications import templates
    now = now or datetime.now()
    if direction == "EXIT":
        kind = "EXIT"
    else:
        kind = "LATE" if tolerance and now.hour * 60 + now.minute > _minutes(tolerance) else "ENTRY"
    return templates.message(kind, student, now, camera, tolerance)


def absence_message(student: Dict[str, Any], tolerance: str, now: Optional[datetime] = None) -> str:
    from app.notifications import templates
    return templates.message("ABSENCE", student, now or datetime.now(), "", tolerance)


def early_exit_message(student: Dict[str, Any], exit_time: str, shift_end: str) -> str:
    return "\n".join(["🟠 <b>Saída antecipada</b>", _who(student),
                      f"Saiu da escola às {escape(exit_time or '—')}, antes do fim do turno ({escape(shift_end or '—')}).",
                      "Se a saída foi combinada com a escola, desconsidere este aviso."])


def security_message(camera: str, now: Optional[datetime] = None, link: str = "") -> str:
    """1º aviso (sem foto): só o essencial e o pedido de decisão no RealGuard."""
    now = now or datetime.now()
    lines = ["⚠️ <b>RealGuard · Possível rosto obstruído</b>", f"📍 {escape(camera)} · 🕒 {now:%H:%M}",
             "Confirme ou descarte em <b>Ocorrências</b> no RealGuard."]
    if link:
        lines.append(escape(link))
    return "\n".join(lines)


CATEGORIES = {
    "nao_identificado": "Pessoa não identificada",
    "acesso_indevido": "Acesso indevido",
    "suspeito": "Comportamento suspeito",
    "outro": "Outro",
}


def _hhmm(value: str) -> str:
    return str(value or "")[11:16]


def security_card_message(event: Dict[str, Any], camera: str, now: Optional[datetime] = None) -> str:
    """Cartão do alerta no grupo de segurança (vai com os botões Ver foto / Confirmar / Descartar)."""
    now = now or datetime.now()
    created = str(event.get("created_at") or "")
    lines = ["⚠️ <b>Possível rosto obstruído</b>", f"📍 {escape(camera)} · 🕒 {_hhmm(created)}"]
    if event.get("reminded_at"):
        try:
            mins = int((now - datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
            lines.append(f"⏳ <b>Ainda aguardando decisão</b> (há {max(0, mins)} min)")
        except ValueError:
            lines.append("⏳ <b>Ainda aguardando decisão</b>")
    lines.append(f"<i>Alerta nº {int(event.get('id') or 0)} · decida pelos botões ou em Ocorrências</i>")
    return "\n".join(lines)


def security_confirmed_message(event: Dict[str, Any], camera: str, by: str) -> str:
    """Relatório completo, enviado só DEPOIS da confirmação humana (vai junto com a foto)."""
    detected, decided = event.get("created_at") or "", event.get("validated_at") or ""
    minutes = ""
    try:
        d = (datetime.strptime(decided[:19], "%Y-%m-%d %H:%M:%S")
             - datetime.strptime(detected[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        minutes = f" ({round(d)} min)" if d >= 0 else ""
    except ValueError:
        pass
    cat = CATEGORIES.get(event.get("category") or "", event.get("category") or "Ocorrência")
    lines = [f"🔴 <b>Ocorrência confirmada · {escape(cat)}</b>",
             f"📍 {escape(camera)}",
             f"🕒 detectada {_hhmm(detected)} · confirmada {_hhmm(decided)} por {escape(by)}{minutes}"
             + (" pelo Telegram" if event.get("decided_via") == "telegram" else ""),
             f"📅 {detected[8:10]}/{detected[5:7]}/{detected[0:4]}" if len(detected) >= 10 else ""]
    if event.get("note"):
        lines.append(f"📝 {escape(event['note'])}")
    return "\n".join(x for x in lines if x)


def security_discarded_message(event: Dict[str, Any], by: str) -> str:
    return f"✔️ Alerta das {_hhmm(event.get('created_at'))} descartado por {escape(by)} (falso alarme)."


def security_reminder_message(event: Dict[str, Any], minutes: int, link: str = "") -> str:
    text = (f"⏳ Alerta das {_hhmm(event.get('created_at'))} ainda aguardando decisão "
            f"(há mais de {int(minutes)} min). Confirme ou descarte em Ocorrências.")
    return text + (f"\n{escape(link)}" if link else "")


def test_message(text: str) -> str:
    return "🧪 <i>Mensagem de teste</i>\n\n" + text
