from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class TimeFrame(Enum):
    """Desteklenen bar timeframe'leri (değer = dakika)."""

    M1 = 1
    M5 = 5
    M15 = 15
    M30 = 30
    H1 = 60
    D1 = 1440

    @property
    def minutes(self) -> int:
        """Timeframe süresi (dakika)."""
        return self.value


@dataclass(frozen=True)
class Bar:
    """Provider'lar arası normalize OHLCV bar.

    datetime bucket başlangıcı; is_closed bar'ın güvenilir biçimde kapanıp
    kapanmadığını söyler.
    """

    symbol: str
    datetime: datetime
    timeframe: TimeFrame
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    is_closed: bool


@dataclass(frozen=True)
class TradingSession:
    """Bir piyasa seansının başlangıç/bitiş saati ve timezone'u."""

    start: time
    end: time
    timezone: ZoneInfo


@dataclass(frozen=True)
class PricePoint:
    """Tek bir zaman damgasındaki fiyat gözlemi."""

    datetime: datetime
    price: float
