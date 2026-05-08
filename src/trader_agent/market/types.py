from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class TimeFrame(Enum):
    """Desteklenen bar timeframe'leri; değerleri dakika cinsindendir."""

    M1 = 1
    M5 = 5
    M15 = 15
    M30 = 30
    H1 = 60
    D1 = 1440

    @property
    def minutes(self) -> int:
        """Timeframe süresini dakika cinsinden döndürür."""
        return self.value


@dataclass(frozen=True)
class Bar:
    """Market provider'ları arasında kullanılan normalize OHLCV bar.

    ``datetime`` bar başlangıcını temsil eder. ``is_closed`` provider'ın bu
    aralığı güvenilir ve tamamlanmış kabul edip etmediğini gösterir.
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
    """Bir piyasa seansının zaman sınırları ve timezone'u."""

    start: time
    end: time
    timezone: ZoneInfo


@dataclass(frozen=True)
class PricePoint:
    """Nokta bazlı veri akışından gelen tek zamanlı fiyat gözlemi."""

    datetime: datetime
    price: float
