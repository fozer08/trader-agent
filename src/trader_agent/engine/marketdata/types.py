from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal


OHLCQuality = Literal["reported", "range_only", "sampled", "close_only"]


@dataclass(frozen=True)
class PriceQuote:
    """Agent ve web için fiyatın zamanı, türü ve karşılaştırma kapanışını birlikte taşır."""

    symbol: str
    price: float | None
    as_of: datetime | None
    kind: Literal["delayed", "close", "unavailable"]
    session_date: date | None = None
    reference_close: float | None = None

    @property
    def change_pct(self) -> float | None:
        if self.price is None or self.reference_close is None or self.reference_close <= 0:
            return None
        return (self.price / self.reference_close - 1) * 100


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
class OHLCV:
    """Bir zaman aralığının başlangıcı ile fiyat ve hacim değerleri.

    Aralığın tamamlanmış olup olmadığı bu veriyi taşıyan yapının sözleşmesidir.
    ohlc_kind, örneklenen veya kapanışla doldurulan aralıkların gerçek OHLC
    gerektiren hesaplarda kullanılmasını önlemek için kaynak kalitesini taşır.
    """

    period_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    ohlc_kind: OHLCQuality = "reported"


@dataclass(frozen=True)
class BarData:
    """Tek sembol ve periyoda ait, zamanına göre artan sırada tamamlanmış barlar.

    Sembol ve periyot bilgisi seriye aittir; boş seriler de bu bilgiyi taşır.
    period_start seans timezone'unda barın başlangıcını gösterir.
    """

    symbol: str
    timeframe: TimeFrame
    bars: list[OHLCV]


@dataclass(frozen=True)
class SessionSnapshot:
    """Seans başlangıcından son alınan veriye kadar günün fiyat özeti.

    ohlcv.period_start seans başlangıcını, as_of özette kullanılan son verinin
    zamanını gösterir. Seans devam ederken fiyat ve hacim değerleri değişir.
    """

    symbol: str
    ohlcv: OHLCV
    as_of: datetime
