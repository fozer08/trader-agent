from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from ..types import Bar, IntradaySnapshot, TimeFrame


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


class MarketDataProvider(ABC):
    """Market data provider'ları için ortak async arayüz."""

    calls_per_minute: int

    @abstractmethod
    async def get_intraday(self, symbol: str, tf: TimeFrame) -> list[Bar]:
        """Bugünün gün içi barlarını istenen timeframe'de döndürür."""

    @abstractmethod
    async def get_today(self, symbol: str) -> IntradaySnapshot | None:
        """Bugünün anlık snapshot'ını döndürür."""

    @abstractmethod
    async def get_daily(self, symbol: str, period: int) -> list[Bar]:
        """Son `period` günlük barı döndürür."""
