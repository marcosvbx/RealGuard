"""
Hash de senha só com a biblioteca padrão (PBKDF2-SHA256), sem pacote extra
— a AIBOX roda Python 3.8 sem acesso ao pip.

Formato guardado: pbkdf2_sha256$<iterações>$<sal base64>$<hash base64>
"""

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 200_000
MIN_LENGTH = 8


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Comparação em tempo constante. Hash vazio/estranho = nunca confere."""
    try:
        algorithm, iterations, salt, digest = (stored or "").split("$")
        if algorithm != ALGORITHM:
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                     base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_problem(password: str, username: str = "") -> str:
    """Mensagem do problema da senha, ou "" se ela é aceitável."""
    if len(password or "") < MIN_LENGTH:
        return f"A senha precisa ter pelo menos {MIN_LENGTH} caracteres."
    if username and password.strip().lower() == username.strip().lower():
        return "A senha não pode ser igual ao usuário."
    if len(set(password)) < 4:
        return "Senha fraca demais: use letras e números variados."
    return ""


def generate_temporary_password() -> str:
    """Senha provisória legível (sem 0/O, 1/l), para o admin repassar ao usuário."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))
