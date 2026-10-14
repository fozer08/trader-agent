from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite

import httpx

from ...utils.logging import get_logger
from .base import MarketDataProvider, ProviderError, ProviderResponseError, ProviderTimeoutError
from .cache import MarketBarCache
from ..calendar import TradingCalendar
from .helpers import (
    aggregate_bars,
    delayed_now,
    expected_period_start,
    ohlcv_from_bars,
)
from .rate_limiter import RateLimiter
from .types import OHLCV, BarData, SessionSnapshot, TimeFrame

_log = get_logger(__name__)


@dataclass(frozen=True)
class _PricePoint:
    """İş Yatırım yanıtından okunan zaman damgası ve fiyat."""

    datetime: datetime
    price: float


class IsYatirimProvider(MarketDataProvider):
    """İş Yatırım yanıtlarını istenen periyottaki barlara dönüştürür.

    Gün içi fiyat noktaları M1 barlar olarak biriktirilir; diğer periyotlar ve
    günün fiyat özeti aynı veriden üretilir. Günlük sonuç önbelleği servistedir.

    HTTP zaman aşımı ve geçici bağlantı hataları için tekrar deneme sayısı,
    oluşturulurken timeout ve max_retries parametreleriyle belirlenir.
    """

    source = "isyatirim"
    calls_per_minute = 15

    delay_minutes = 15   # İş Yatırım ücretsiz feed gecikmesi (dakika)
    user_agent = "Mozilla/5.0"

    _source_tf = TimeFrame.M1         # İş Yatırım için seçtiğimiz kaynak çözünürlüğü
    _daily_fetch_days = 200           # Günlük fetch penceresinin uzunluğu (gün)
    _daily_fetch_padding_days = 10    # Trading day hesabındaki tatil/hafta sonu tamponu

    intraday_url = (
        "https://www.isyatirim.com.tr/_Layouts/15/"
        "IsYatirim.Website/Common/ChartData.aspx/IndexHistoricalAll"
    )

    historical_url = (
        "https://www.isyatirim.com.tr/_layouts/15/"
        "Isyatirim.Website/Common/Data.aspx/HisseTekil"
    )

    def __init__(
        self,
        calendar: TradingCalendar,
        timeout: float = 30,
        _transport: httpx.AsyncBaseTransport | None = None,
        *,
        store: MarketBarCache | None = None,
        max_retries: int = 1,
    ) -> None:
        self.calendar = calendar
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            transport=_transport,
        )
        self._max_retries = max_retries
        self._rate_limiter = RateLimiter(self.calls_per_minute)
        self._store = store if store is not None else MarketBarCache()
        self._intraday_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> IsYatirimProvider:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    # ---- Public API --------------------------------------------------------

    async def get_intraday(self, symbol: str, tf: TimeFrame) -> BarData:
        """M1 önbelleğinden bugünün tamamlanmış barlarını istenen periyotta üretir."""
        if tf is TimeFrame.D1:
            raise ValueError("Use get_daily() for daily bars.")
        symbol = self._normalize_symbol(symbol)
        bounds = self._session_bounds()
        if bounds is None:
            return BarData(symbol=symbol, timeframe=tf, bars=[])
        start, end = bounds
        source_series = await self._get_source_bars(symbol, start, end)
        return aggregate_bars(
            series=source_series,
            target_tf=tf,
            closed_until=end,
            session_start=start.timetz().replace(tzinfo=None),
        )

    async def get_today(self, symbol: str) -> SessionSnapshot | None:
        """Günün fiyat özetini biriktirilmiş M1 barlarından üretir."""
        symbol = self._normalize_symbol(symbol)
        bounds = self._session_bounds()
        if bounds is None:
            return None
        start, end = bounds
        source_bars = (await self._get_source_bars(symbol, start, end)).bars
        if not source_bars:
            return None
        open_, high, low, close, volume = ohlcv_from_bars(source_bars)
        return SessionSnapshot(
            symbol=symbol,
            ohlcv=OHLCV(
                period_start=start,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                ohlc_kind="sampled",
            ),
            as_of=source_bars[-1].period_start,
        )

    async def get_daily(self, symbol: str, period: int) -> BarData:
        """Son `period` adet günlük barı upstream'den çeker.

        Geniş bir tarih aralığı sorgulanır; yeterli bar dönmezse en fazla iki kez
        daha genişletilir. Sonuç gerçek bar sayısına göre son `period` ile sınırlanır.
        """
        if period <= 0:
            raise ValueError("period must be positive.")

        symbol = self._normalize_symbol(symbol)
        start_dt, end_dt = self._daily_fetch_range(period)
        # İstenen bar sayısına göre sınırlı genişletme; yeni hisselerde sonsuz
        # tarih taraması yapılmaz. Bilinen tatiller ve haftalık düzen uygulanır.
        series = BarData(symbol, TimeFrame.D1, [])
        for attempt in range(3):
            rows = await self._fetch_daily_rows(symbol, start_dt.date(), end_dt.date())
            converted = self._daily_rows_to_bars(symbol, rows)
            series = BarData(symbol, TimeFrame.D1, [
                bar for bar in converted.bars if start_dt <= bar.period_start <= end_dt
            ])
            if len(series.bars) >= period or not series.bars or attempt == 2:
                break
            start_dt = self.calendar.next_session(
                start_dt.date() - timedelta(days=max(200, (end_dt - start_dt).days)),
                inclusive=True, allow_uncovered=True,
            ).start
        return BarData(
            symbol=series.symbol,
            timeframe=series.timeframe,
            bars=series.bars[-period:],
        )

    # ---- Source bars and request windows -----------------------------------

    def _now(self) -> datetime:
        return datetime.now(self.calendar.timezone)

    def _session_bounds(self) -> tuple[datetime, datetime] | None:
        """Şu an için güvenilir kabul edilen seans aralığı (feed gecikmesi düşülmüş)."""
        now = self._now()
        return self.calendar.reliable_bounds(
            delayed_now(now, self.delay_minutes), on=now.date(),
        )

    def _daily_fetch_range(self, period: int) -> tuple[datetime, datetime]:
        """API'den çekilecek (start, end) aralığı; period için yeterli takvim günü içerir."""
        now = self._now()
        days = max(
            self._daily_fetch_days,
            int(period * 7 / len(self.calendar.schedule.weekdays)) + self._daily_fetch_padding_days,
        )
        start = self.calendar.next_session(
            now.date() - timedelta(days=days), inclusive=True, allow_uncovered=True,
        ).start
        end = self.calendar.last_completed_session(
            delayed_now(now, self.delay_minutes), allow_uncovered=True,
        ).start
        return start, end

    async def _get_source_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> BarData:
        """M1 verisinin eksik aralığını tamamlar ve biriktirilmiş barları döndürür."""
        cache_end = expected_period_start(self._source_tf, self.calendar, end)
        if cache_end is None or cache_end < start:
            return BarData(symbol=symbol, timeframe=self._source_tf, bars=[])

        lock = self._intraday_locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            missing = self._store.missing_tail(symbol, self._source_tf, start, cache_end)
            if missing is not None:
                series = await self._fetch_intraday_source_bars(symbol, missing[0], end)
                if series.bars:
                    self._store.set(series)

        return self._store.get(symbol, self._source_tf, start, cache_end)

    async def _fetch_intraday_source_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> BarData:
        """[start, end] aralığındaki M1 barlarını kaynaktan çeker."""
        points = await self._fetch_intraday_points(symbol, start, end)
        return self._price_points_to_bars(
            symbol, [point for point in points if point.datetime >= start], end,
        )

    # ---- Internal helpers --------------------------------------------------

    async def _fetch_intraday_points(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[_PricePoint]:
        """Chart endpoint'inden ham fiyat noktalarını çeker."""
        params: dict[str, str | int] = {
            "period": self._source_tf.minutes,
            "from": start.strftime("%Y%m%d%H%M%S"),
            "to": end.strftime("%Y%m%d%H%M%S"),
            "endeks": symbol,
        }

        payload = await self._get_json(self.intraday_url, params, symbol)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ProviderResponseError(f"Unexpected intraday payload type: {type(rows)}")

        points: list[_PricePoint] = []
        for index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) < 2:
                raise ProviderResponseError(f"Invalid intraday row [{symbol}] index={index}")
            timestamp_ms, price = row[0], row[1]

            # API epoch millisaniye döndürür; UTC'ye çevir, sonra seans timezone'una al
            try:
                dt = datetime.fromtimestamp(
                    self._number(timestamp_ms, "timestamp") / 1000,
                    timezone.utc,
                ).astimezone(self.calendar.timezone)
                point = _PricePoint(datetime=dt, price=self._number(price, "price"))
            except (ValueError, OverflowError, OSError, ProviderResponseError) as exc:
                raise ProviderResponseError(
                    f"Invalid intraday point [{symbol}] index={index}: {exc}"
                ) from exc
            points.append(point)

        return sorted(points, key=lambda p: p.datetime)

    def _price_points_to_bars(
        self,
        symbol: str,
        points: list[_PricePoint],
        closed_until: datetime,
    ) -> BarData:
        """Kapanmış fiyat noktalarını M1 barlarına çevirir (O=H=L=C, volume yok)."""
        window = timedelta(minutes=self._source_tf.minutes)
        bars = [
            OHLCV(
                period_start=p.datetime,
                open=p.price,
                high=p.price,
                low=p.price,
                close=p.price,
                volume=None,
                ohlc_kind="sampled",
            )
            for p in points
            if p.datetime + window <= closed_until
        ]
        return BarData(symbol=symbol, timeframe=self._source_tf, bars=bars)

    async def _fetch_daily_rows(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[dict[str, object]]:
        """Historical endpoint'ten ham günlük satırları çeker."""
        params: dict[str, str | int] = {
            "hisse": symbol,
            "startdate": start.strftime("%d-%m-%Y"),
            "enddate": end.strftime("%d-%m-%Y"),
        }
        payload = await self._get_json(self.historical_url, params, symbol)

        if payload.get("ok") is not True:
            raise ProviderResponseError(f"IsYatirim daily response is not ok [{symbol}]")

        rows = payload.get("value")
        if not isinstance(rows, list):
            raise ProviderResponseError(f"Unexpected daily payload type: {type(rows)}")
        result: list[dict[str, object]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ProviderResponseError(f"Invalid daily row [{symbol}] index={index}")
            result.append(row)
        return result

    # ---- Convert -----------------------------------------------------------

    def _daily_rows_to_bars(self, symbol: str, rows: list[dict[str, object]]) -> BarData:
        """Ham günlük satırları normalize D1 OHLCV'larına çevirir."""
        bars: list[OHLCV] = []

        for row in rows:
            bar_date = self._parse_daily_date(row)
            day = self.calendar.day(bar_date, allow_uncovered=True)
            if day.session is None:
                continue

            # Kapanış zorunlu; diğer OHLC alanları yoksa kapanıştan türetilir.
            close = self._pick_float(row, "HG_KAPANIS", "HGDG_KAPANIS")
            if close is None:
                raise ProviderResponseError(f"Missing daily close [{symbol}] date={bar_date}")

            raw_open = self._pick_float(row, "HG_ACILIS", "HGDG_ACILIS")
            raw_high = self._pick_float(row, "HG_MAX", "HGDG_MAX")
            raw_low = self._pick_float(row, "HG_MIN", "HGDG_MIN")

            # API doğrudan volume vermez; ciro (TL) / ağırlıklı ortalama fiyat = lot adedi
            turnover = self._pick_float(row, "HG_HACIM", "HGDG_HACIM", allow_zero=True)
            avg_price = self._pick_float(row, "HG_AOF", "HGDG_AOF")
            volume = (
                self._number(turnover / avg_price, "volume", allow_zero=True)
                if turnover is not None and avg_price is not None and avg_price > 0
                else None
            )

            range_reported = raw_high is not None and raw_low is not None
            body_low, body_high = min(raw_open or close, close), max(raw_open or close, close)
            if ((raw_low is not None and raw_low > body_low)
                    or (raw_high is not None and raw_high < body_high)):
                raise ProviderResponseError(f"Inconsistent daily OHLC [{symbol}] date={bar_date}")

            bars.append(
                OHLCV(
                    period_start=day.session.start,
                    open=close if raw_open is None else raw_open,
                    high=close if raw_high is None else raw_high,
                    low=close if raw_low is None else raw_low,
                    close=close,
                    volume=volume,
                    ohlc_kind=("reported" if range_reported and raw_open is not None
                               else "range_only" if range_reported else "close_only"),
                )
            )

        return BarData(
            symbol=symbol,
            timeframe=TimeFrame.D1,
            bars=sorted(bars, key=lambda b: b.period_start),
        )

    # ---- HTTP --------------------------------------------------------------

    async def _get_json(
        self, url: str, params: dict[str, str | int], symbol: str,
    ) -> dict[str, object]:
        """Endpoint'ten JSON çeker, bozuk yanıtta açıklayıcı hata fırlatır."""
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            await self._rate_limiter.acquire()
            started = time.monotonic()
            try:
                response = await self.client.get(
                    url,
                    params=params,
                    headers={
                        "Referer": self._referer_url(symbol),
                        # İş Yatırım AJAX isteği bekliyor; bu header olmadan 403 döner
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                break
            except httpx.RequestError as exc:
                retryable = isinstance(
                    exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
                )
                retry = retryable and attempt < attempts
                log = _log.warning if retry else _log.error
                log(
                    "HTTP isteği başarısız [%s] %s (deneme %d/%d, %dms): %s: %s",
                    symbol,
                    url.rsplit("/", 1)[-1],
                    attempt,
                    attempts,
                    int((time.monotonic() - started) * 1000),
                    type(exc).__name__,
                    str(exc) or "Yanıt alınamadı.",
                )
                if not retry:
                    if isinstance(exc, httpx.TimeoutException):
                        raise ProviderTimeoutError(f"Provider timed out [{symbol}]") from exc
                    raise ProviderError(f"Provider request failed [{symbol}]: {type(exc).__name__}") from exc
                await asyncio.sleep(min(2 ** (attempt - 1), 8))

        _log.debug(
            "provider [%s] %s params=%s -> %d in %dms",
            symbol,
            url.rsplit("/", 1)[-1],
            params,
            response.status_code,
            int((time.monotonic() - started) * 1000),
        )

        if response.status_code == 429:
            _log.warning("Rate limit yanıtı [%s]: HTTP 429", symbol)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.error("HTTP hata [%s]: %s %s", symbol, exc.response.status_code, exc.response.url)
            raise ProviderResponseError(
                f"Provider HTTP {response.status_code} [{symbol}]"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                "Invalid JSON response from IsYatirim. "
                f"status={response.status_code}, "
                f"symbol={symbol}"
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderResponseError(f"Unexpected JSON payload type: {type(payload)}")
        return payload

    # ---- İş Yatırım helpers ------------------------------------------------

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Sembolü trim'leyip büyük harfe çevirir, boşsa hata fırlatır."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol cannot be empty.")
        return symbol

    @staticmethod
    def _referer_url(symbol: str) -> str:
        """Endpoint'in beklediği hisse kartı referer URL'ini üretir."""
        return (
            "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/"
            f"Sayfalar/sirket-karti.aspx?hisse={symbol}"
        )

    @staticmethod
    def _parse_daily_date(row: dict[str, object]) -> date:
        """Daily satırından bilinen alan adları ve formatlardan tarihi okur."""
        value = row.get("HGDG_TARIH") or row.get("HG_TARIH")
        if not value:
            raise ProviderResponseError("Missing daily date")
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value

        value = str(value).strip()
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ProviderResponseError("Invalid daily date")

    @classmethod
    def _pick_float(
        cls, row: dict[str, object], *keys: str, allow_zero: bool = False,
    ) -> float | None:
        """İlk dolu alanı okur; eksik alan None, bozuk değer hata döndürür."""
        for key in keys:
            value = row.get(key)
            if value is not None:
                return cls._number(value, key, allow_zero=allow_zero)
        return None

    @staticmethod
    def _number(value: object, field: str, *, allow_zero: bool = False) -> float:
        """Dış kaynaktan gelen sonlu, pozitif sayı; hacim için sıfır kabul edilir."""
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProviderResponseError(f"Invalid numeric field: {field}")
        if isinstance(value, str):
            value = value.strip()
            if "," in value:
                value = value.replace(".", "").replace(",", ".")
        try:
            number = float(value)
        except (ValueError, OverflowError) as exc:
            raise ProviderResponseError(f"Invalid numeric field: {field}") from exc
        if not isfinite(number) or number < 0 or (number == 0 and not allow_zero):
            raise ProviderResponseError(f"Invalid numeric field: {field}")
        return number
