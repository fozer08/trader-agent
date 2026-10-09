from __future__ import annotations

from abc import ABC, abstractmethod

from .types import BarData, SessionSnapshot, TimeFrame
from ..calendar import TradingCalendar


class ProviderError(Exception):
    """Piyasa verisi sağlayıcısının erişim veya yanıt hatası."""


class ProviderTimeoutError(ProviderError):
    """Veri sağlayıcısı zamanında yanıt vermedi."""


class ProviderResponseError(ProviderError):
    """Veri sağlayıcısının yanıtı okunamadığında veya beklenen biçimde olmadığında oluşur."""


class MarketDataProvider(ABC):
    """İstenen periyotta tamamlanmış barlar ve günün fiyat özetini sağlar.

    Ham verinin biçimi, çözünürlüğü ve barların nasıl üretildiği sağlayıcının
    sorumluluğundadır.
    """

    source: str
    delay_minutes: int
    calendar: TradingCalendar

    @abstractmethod
    async def get_daily(self, symbol: str, period: int) -> BarData:
        """D1 serisini döndürür; yeterli geçmiş yoksa `period`'dan az bar içerir."""

    @abstractmethod
    async def get_intraday(self, symbol: str, tf: TimeFrame) -> BarData:
        """Bugünün tamamlanmış barlarını sembol ve istenen periyot bilgisiyle döndürür."""

    @abstractmethod
    async def get_today(self, symbol: str) -> SessionSnapshot | None:
        """Bugünün fiyat özetini ve son veri zamanını döndürür; veri yoksa None."""
