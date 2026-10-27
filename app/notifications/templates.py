"""
Mensagens aos responsáveis editáveis pela escola, uma por uma (Escola → Integrações).

O texto usa marcadores amigáveis — na tela eles aparecem como etiquetas ("Nome do aluno",
"Horário"...), nunca como código. A primeira linha vai em negrito e o nome do aluno também.
Sem texto salvo, vale o modelo padrão (o mesmo que o RealGuard sempre mandou).
"""

import re
import time
from html import escape
from typing import Any, Dict, Optional

from app.school.rm import display_rm

KEY = "guardian_message_templates"

KINDS = {
    "ENTRY": "Entrada no horário",
    "LATE": "Entrada com atraso",
    "EXIT": "Saída",
    "ABSENCE": "Ausência",
}

VARIABLES = {
    "aluno": "Nome do aluno",
    "turma": "Turma",
    "rm": "RM",
    "hora": "Horário",
    "data": "Data",
    "local": "Local (câmera)",
    "horario_limite": "Horário-limite",
}

DEFAULTS = {
    "ENTRY": "🟢 Entrada registrada\n👤 {aluno}\n🎓 {turma} · {rm}\n🕒 {hora} · {data}\n✅ Chegou dentro do horário\n📍 {local}",
    "LATE": "🟢 Entrada registrada\n👤 {aluno}\n🎓 {turma} · {rm}\n🕒 {hora} · {data}\n"
            "⏰ Chegada com atraso (tolerância {horario_limite})\n📍 {local}",
    "EXIT": "🔴 Saída registrada\n👤 {aluno}\n🎓 {turma} · {rm}\n🕒 {hora} · {data}\n📍 {local}",
    "ABSENCE": "⚠️ Ausência hoje\n👤 {aluno}\n🎓 {turma} · {rm}\nAinda não registrou entrada (tolerância {horario_limite}).\n"
               "Se já estiver a caminho, desconsidere este aviso.",
}

MAX_LEN = 600
_TOKEN = re.compile(r"\{(\w+)\}")
_cache: Dict[str, Any] = {"at": 0.0, "data": None}


class TemplateError(ValueError):
    pass


def custom() -> Dict[str, str]:
    """Textos salvos pela escola (cache curto: a mensagem sai a cada passagem na câmera)."""
    if _cache["data"] is not None and time.monotonic() - _cache["at"] < 10:
        return _cache["data"]
    try:
        from database import system_settings as ss
        data = {k: v for k, v in (ss._json(KEY, {}) or {}).items() if k in KINDS and isinstance(v, str) and v.strip()}
    except Exception:
        data = {}
    _cache.update(at=time.monotonic(), data=data)
    return data


def text_for(kind: str) -> str:
    return custom().get(kind) or DEFAULTS[kind]


def validate(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    if len(text) > MAX_LEN:
        raise TemplateError(f"Mensagem muito longa (máximo {MAX_LEN} caracteres).")
    unknown = [k for k in _TOKEN.findall(text) if k not in VARIABLES]
    if unknown:
        raise TemplateError(f"Informação desconhecida: {{{unknown[0]}}}.")
    return text


def save(kind: str, text: str, by: str = "") -> Dict[str, str]:
    """Salva (ou, com texto vazio, volta ao padrão) a mensagem de um tipo."""
    if kind not in KINDS:
        raise TemplateError("Tipo de mensagem inválido.")
    from database import system_settings as ss
    data = dict(ss._json(KEY, {}) or {})
    clean = validate(text)
    if clean and clean != DEFAULTS[kind]:
        data[kind] = clean
    else:
        data.pop(kind, None)
    ss._save_json(KEY, data, by)
    _cache.update(at=0.0, data=None)
    return data


def values_for(student: Dict[str, Any], when, camera: str = "", deadline: str = "") -> Dict[str, str]:
    return {
        "aluno": student.get("name") or "",
        "turma": student.get("class_name") or "",
        "rm": display_rm(student["registration_number"]) if student.get("registration_number") else "",
        "hora": f"{when:%H:%M}",
        "data": f"{when:%d/%m/%Y}",
        "local": camera or "",
        "horario_limite": deadline or "",
    }


def _tidy(line: str) -> str:
    """Tira separadores que sobraram quando uma informação veio vazia ('7º A · ' → '7º A')."""
    line = re.sub(r"(\s*·\s*){2,}", " · ", line)
    line = re.sub(r"^(\S*\s*)·\s*", r"\1", line) if re.match(r"^\S*\s*·", line) else line
    return re.sub(r"\s*·\s*$", "", line).rstrip()


def render(text: str, values: Dict[str, str]) -> str:
    """Texto do modelo → HTML do Telegram. Valores sempre escapados."""
    out = []
    for i, raw in enumerate(text.split("\n")):
        keys = _TOKEN.findall(raw)
        if keys and all(not values.get(k) for k in keys) and not re.search(r"[^\W\d_]", _TOKEN.sub("", raw)):
            continue     # linha só de informações vazias (ex.: aluno sem turma nem RM): some
        parts, pos = [], 0
        for m in _TOKEN.finditer(raw):
            parts.append(escape(raw[pos:m.start()]))
            v = escape(values.get(m.group(1), ""))
            parts.append(f"<b>{v}</b>" if m.group(1) == "aluno" and v and i else v)
            pos = m.end()
        parts.append(escape(raw[pos:]))
        line = _tidy("".join(parts))
        if not line.strip():
            continue
        out.append(f"<b>{line}</b>" if i == 0 and line else line)
    return "\n".join(out)


def message(kind: str, student: Dict[str, Any], when, camera: str = "", deadline: str = "",
            text: Optional[str] = None) -> str:
    return render(text if text is not None else text_for(kind), values_for(student, when, camera, deadline))
