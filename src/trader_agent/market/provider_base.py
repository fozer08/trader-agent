from __future__ import annotations

from abc import ABC, abstractmethod

from .types import Bar, IntradaySnapshot, TimeFrame


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
