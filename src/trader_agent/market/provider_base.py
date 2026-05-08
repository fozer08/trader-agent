from __future__ import annotations

from abc import ABC, abstractmethod

from .types import TimeFrame, Bar


class MarketDataProvider(ABC):
    """Market data provider'ları için ortak async arayüz.

    Alt sınıflar ``calls_per_minute`` sınıf değişkenini override ederek
    kendi rate limit'lerini bildirir.
    """

    calls_per_minute: int | None = None

    @abstractmethod
    async def get_intraday(
        self,
        symbol: str,
        tf: TimeFrame,
    ) -> list[Bar]:
        """``symbol`` için istenen timeframe'de gün içi barları döndürür."""
        raise NotImplementedError

    @abstractmethod
    async def get_today(
        self,
        symbol: str,
    ) -> Bar | None:
        """``symbol`` için mevcut en güncel günlük-benzeri görünümü döndürür."""
        raise NotImplementedError

    @abstractmethod
    async def get_daily(
        self,
        symbol: str,
        period: int,
    ) -> list[Bar]:
        """``symbol`` için son günlük barları döndürür."""
        raise NotImplementedError
