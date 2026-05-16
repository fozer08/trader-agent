from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


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
    """Provider'lar arası normalize tamamlanmış OHLCV bar."""

    symbol: str
    datetime: datetime
    timeframe: TimeFrame
    open: float
    high: float
    low: float
    close: float
    volume: float | None


@dataclass(frozen=True)
class IntradaySnapshot:
    """Seans içi anlık fiyat özeti."""

    symbol: str
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
