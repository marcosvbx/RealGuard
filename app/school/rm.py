"""
RM no padrão FIAP: "RM" + 6 números (ex.: RM566217).

normalize_rm() aceita o que a pessoa costuma digitar — "566217", "rm566217",
"RM 566.217" — e devolve "RM566217". Qualquer outra coisa é recusada com uma
mensagem pronta para a tela.
"""

import re

RM_RE = re.compile(r"^RM\d{6}$")
EXAMPLE = "RM566217"


class RMError(ValueError):
    pass


def normalize_rm(value: str) -> str:
    raw = re.sub(r"[\s.\-/]", "", str(value or "")).upper()
    if re.fullmatch(r"\d{6}", raw):
        raw = "RM" + raw
    if not RM_RE.match(raw):
        raise RMError(f"RM inválido: use RM + 6 números (ex.: {EXAMPLE}). Pode digitar só os números.")
    return raw


def is_valid_rm(value: str) -> bool:
    return bool(RM_RE.match(str(value or "")))


def display_rm(value: str) -> str:
    """Para mostrar: "RM566217" fica como está; RM antigo sem prefixo ("1042") vira "RM 1042"."""
    value = str(value or "").strip()
    if not value:
        return ""
    return value if value.upper().startswith("RM") else f"RM {value}"
