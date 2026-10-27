"""Relógio de negócio: suporta a demonstração também no Windows.

Linux já aplica o deslocamento via TZ; não somá-lo novamente.
Relógios monotônicos, autenticação e timeouts continuam no tempo real.
"""
import os
import time
from datetime import datetime as RealDateTime, date as RealDate, timedelta


def offset():
    # No Linux o ajuste de horário já é aplicado pelo TZ do processo.
    # A data da demonstração é sempre ajustada aqui, em ambas as plataformas.
    try:
        days = max(-3650, min(3650, int(os.getenv('RELOGIO_AJUSTE_DIAS', '0') or 0)))
    except ValueError:
        days = 0
    minutes = 0
    if not hasattr(time, 'tzset'):
        try:
            minutes = max(-1439, min(1439, int(float(os.getenv('RELOGIO_AJUSTE_MIN', '0') or 0))))
        except ValueError:
            minutes = 0
    return timedelta(days=days, minutes=minutes)


class datetime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return super().now(tz) + (offset() if tz is None else timedelta())

    @classmethod
    def today(cls):
        return cls.now()


class date(RealDate):
    @classmethod
    def today(cls):
        d = datetime.now()
        return cls(d.year, d.month, d.day)
