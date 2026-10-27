"""
Feriados nacionais do Brasil calculados sem internet (a rede da AIBOX é isolada).

Fixos (Lei 662/1949, 6.802/1980, 14.759/2023) + móveis a partir da Páscoa:
Carnaval (segunda e terça — ponto facultativo, mas sem aula na prática),
Sexta-feira Santa e Corpus Christi. Feriados estaduais/municipais e recessos
são cadastrados pela escola no calendário.
"""

from datetime import date, timedelta
from typing import List, Tuple


def easter(year: int) -> date:
    """Domingo de Páscoa (algoritmo anônimo gregoriano / Meeus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def national_holidays(year: int) -> List[Tuple[date, str]]:
    p = easter(year)
    items = [
        (date(year, 1, 1), "Confraternização Universal"),
        (p - timedelta(days=48), "Carnaval (segunda-feira)"),
        (p - timedelta(days=47), "Carnaval (terça-feira)"),
        (p - timedelta(days=2), "Sexta-feira Santa"),
        (date(year, 4, 21), "Tiradentes"),
        (date(year, 5, 1), "Dia do Trabalho"),
        (p + timedelta(days=60), "Corpus Christi"),
        (date(year, 9, 7), "Independência do Brasil"),
        (date(year, 10, 12), "Nossa Senhora Aparecida"),
        (date(year, 11, 2), "Finados"),
        (date(year, 11, 15), "Proclamação da República"),
        (date(year, 11, 20), "Dia Nacional de Zumbi e da Consciência Negra"),
        (date(year, 12, 25), "Natal"),
    ]
    return sorted(items)
