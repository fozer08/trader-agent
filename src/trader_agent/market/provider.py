from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx

from ..utils.logging import get_logger

_log = get_logger(__name__)

from .cache import MarketDailyCache, MarketIntradayCache
from .helpers import aggregate_bars, calc_session_bounds, to_timezone
from .provider_base import MarketDataProvider
from .rate_limiter import RateLimiter
from .types import Bar, PricePoint, TimeFrame, TradingSession


class IsYatirimProvider(MarketDataProvider):
    """İş Yatırım'ın public chart endpoint'lerini kullanan market data provider.

    Gün içi veri noktasal fiyatlar olarak çekilir ve opsiyonel aggregation
    öncesinde kaynak M1 barlara normalize edilir. Günlük veri historical stock
    endpoint'inden gelir ve intraday snapshot'lardan ayrı tutulur.
    """

    source = "isyatirim"
    supported_exchanges = {"bist"}
    calls_per_minute = 10

    delay_minutes = 15
    user_agent = "Mozilla/5.0"

    source_tf = TimeFrame.M1

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
        session: TradingSession,
        timeout: int = 10,
        cache: MarketIntradayCache | None = None,
        daily_cache: MarketDailyCache | None = None,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """HTTP client, gün içi ve günlük cache ile async provider oluşturur."""
        self.session = session
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=timeout,
            transport=_transport,
        )
        self.cache = cache or MarketIntradayCache(session_start=session.start)
        self._daily_cache = daily_cache or MarketDailyCache(session=session, delay_minutes=self.delay_minutes)
        self._rate_limiter = RateLimiter(self.calls_per_minute)
        self._fetch_locks: dict[str, asyncio.Lock] = {}
        self._daily_fetch_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> IsYatirimProvider:
        """Ek kaynak açmadan async context manager'a girer."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Context manager'dan çıkarken alttaki HTTP client'ı kapatır."""
        await self.aclose()

    async def aclose(self) -> None:
        """Provider'ın HTTP client'ını kapatır."""
        await self.client.aclose()

    # ---- Public API --------------------------------------------------------

    async def get_intraday(
        self,
        symbol: str,
        tf: TimeFrame,
    ) -> list[Bar]:
        """Bugünün gün içi barlarını ``tf`` timeframe'ine aggregate ederek döndürür.

        Provider yalnızca gecikme hesaba katıldıktan sonra güvenilir kabul
        edilen seans aralığındaki barları döndürür. Günlük barlar bu metodun
        kapsamına özellikle dahil edilmez.
        """
        if tf is TimeFrame.D1:
            raise ValueError("Use get_daily() for daily bars.")

        symbol = self._normalize_symbol(symbol)

        bounds = self._session_bounds()
        if bounds is None:
            return []

        start, end = bounds

        source_bars = await self._get_intraday_bars(
            symbol=symbol,
            start=start,
            end=end,
        )

        bars = aggregate_bars(
            bars=source_bars,
            target_tf=tf,
            closed_until=end,
            session_start=self.session.start,
        )

        return bars

    async def get_today(
        self,
        symbol: str,
    ) -> Bar | None:
        """Bugünkü intraday veriden üretilmiş günlük-benzeri snapshot döndürür.

        Bu, canlı "günün şu ana kadarki durumu" görünümü için kullanışlıdır.
        Daily endpoint'ten gelmez; intraday nokta verisi hacim taşımadığı için
        ``volume=None`` olabilir.
        """
        symbol = self._normalize_symbol(symbol)

        bounds = self._session_bounds()
        if bounds is None:
            return None

        start, end = bounds
        source_bars = await self._get_intraday_bars(
            symbol=symbol,
            start=start,
            end=end,
        )
        bars = aggregate_bars(
            bars=source_bars,
            target_tf=TimeFrame.M5,
            closed_until=end,
            session_start=self.session.start,
        )

        if not bars:
            return None

        high = bars[0].high
        low = bars[0].low
        volume = 0.0
        has_full_volume = True

        for bar in bars:
            if bar.high > high:
                high = bar.high
            if bar.low < low:
                low = bar.low
            if bar.volume is None:
                has_full_volume = False
            elif has_full_volume:
                volume += bar.volume

        return Bar(
            symbol=symbol,
            datetime=start,
            timeframe=TimeFrame.D1,
            open=bars[0].open,
            high=high,
            low=low,
            close=bars[-1].close,
            volume=volume if has_full_volume else None,
            is_closed=end.time() >= self.session.end,
        )

    async def get_daily(
        self,
        symbol: str,
        period: int,
    ) -> list[Bar]:
        """Daily endpoint'ten son ``period`` adet günlük barı döndürür.

        Sonuç cache'lenir; aynı seans içinde tekrarlanan çağrılar API'ye gitmez.
        Eş zamanlı çağrılarda sembol başına lock ile tek fetch garantilenir.
        """
        if period <= 0:
            raise ValueError("period must be positive.")

        symbol = self._normalize_symbol(symbol)

        cached = self._daily_cache.get(symbol, period)
        if cached is not None:
            return cached

        if symbol not in self._daily_fetch_locks:
            self._daily_fetch_locks[symbol] = asyncio.Lock()

        async with self._daily_fetch_locks[symbol]:
            cached = self._daily_cache.get(symbol, period)
            if cached is not None:
                return cached

            fetch_period = max(period, 100)
            end = datetime.now(self.session.timezone).date()
            start = end - timedelta(days=fetch_period + 7 - 1)

            rows = await self._fetch_daily_rows(symbol=symbol, start=start, end=end)
            bars = self._daily_rows_to_bars(symbol, rows)
            self._daily_cache.set(symbol, bars)
            return bars[-period:]

    def _session_bounds(self) -> tuple[datetime, datetime] | None:
        """Provider için o anki güvenilir gün içi seans aralığını döndürür."""
        return calc_session_bounds(
            now=datetime.now(self.session.timezone),
            session_start=self.session.start,
            session_end=self.session.end,
            delay_minutes=self.delay_minutes,
        )

    # ---- Fetch -------------------------------------------------------------

    async def _get_intraday_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Kaynak M1 gün içi barlarını döndürür, sadece cache eksiklerini çeker.

        Sembol başına lock kullanarak eş zamanlı çağrıların aynı veriyi
        tekrar tekrar çekmesini önler.
        """
        if symbol not in self._fetch_locks:
            self._fetch_locks[symbol] = asyncio.Lock()

        async with self._fetch_locks[symbol]:
            missing = self.cache.missing_range(
                symbol,
                self.source_tf,
                end=end,
            )

            if missing is not None:
                missing_start, missing_end = missing
                points = await self._fetch_intraday_points(
                    symbol=symbol,
                    start=missing_start,
                    end=missing_end,
                )
                source_bars = self._price_points_to_bars(
                    symbol=symbol,
                    points=points,
                    closed_until=end,
                )
                self.cache.merge(symbol, self.source_tf, source_bars)

        return self.cache.get(
            symbol,
            self.source_tf,
            start=start,
            end=end,
        )

    async def _fetch_intraday_points(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PricePoint]:
        """İş Yatırım chart endpoint'inden ham gün içi fiyat noktalarını çeker."""
        start = to_timezone(start, self.session.timezone)
        end = to_timezone(end, self.session.timezone)

        params = {
            "period": self.source_tf.minutes,
            "from": start.strftime("%Y%m%d%H%M%S"),
            "to": end.strftime("%Y%m%d%H%M%S"),
            "endeks": symbol,
        }

        payload = await self._get_json(
            url=self.intraday_url,
            params=params,
            symbol=symbol,
        )

        rows = payload.get("data", [])

        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected intraday payload type: {type(rows)}")

        points: list[PricePoint] = []

        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue

            timestamp_ms, price = row[0], row[1]

            if timestamp_ms is None or price is None:
                continue

            dt = datetime.fromtimestamp(
                float(timestamp_ms) / 1000,
                timezone.utc,
            ).astimezone(self.session.timezone)

            points.append(
                PricePoint(
                    datetime=dt,
                    price=float(price),
                )
            )

        return sorted(points, key=lambda point: point.datetime)

    def _price_points_to_bars(
        self,
        symbol: str,
        points: list[PricePoint],
        closed_until: datetime,
    ) -> list[Bar]:
        """Noktasal fiyatları kaynak timeframe barlarına çevirir."""
        return [
            Bar(
                symbol=symbol,
                datetime=point.datetime,
                timeframe=self.source_tf,
                open=point.price,
                high=point.price,
                low=point.price,
                close=point.price,
                volume=None,
                is_closed=point.datetime
                + timedelta(minutes=self.source_tf.minutes) <= closed_until,
            )
            for point in points
        ]

    async def _fetch_daily_rows(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[dict]:
        """İş Yatırım historical stock endpoint'inden ham günlük satırları çeker."""
        params = {
            "hisse": symbol,
            "startdate": start.strftime("%d-%m-%Y"),
            "enddate": end.strftime("%d-%m-%Y"),
        }

        payload = await self._get_json(
            url=self.historical_url,
            params=params,
            symbol=symbol,
        )

        if not payload.get("ok"):
            raise RuntimeError(f"IsYatirim daily response is not ok: {payload}")

        rows = payload.get("value", [])

        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected daily payload type: {type(rows)}")

        return rows

    # ---- Convert -----------------------------------------------------------

    def _daily_rows_to_bars(
        self,
        symbol: str,
        rows: list[dict],
    ) -> list[Bar]:
        """İş Yatırım günlük satırlarını normalize D1 barlara çevirir."""
        bars: list[Bar] = []

        for row in rows:
            bar_date = self._parse_daily_date(row)

            if bar_date is None:
                continue

            close = self._pick_float(row, "HG_KAPANIS", "HGDG_KAPANIS")

            if close is None:
                continue

            _open = self._pick_float(row, "HG_ACILIS", "HGDG_ACILIS")
            _high = self._pick_float(row, "HG_MAX", "HGDG_MAX")
            _low = self._pick_float(row, "HG_MIN", "HGDG_MIN")
            open_ = close if _open is None else _open
            high = close if _high is None else _high
            low = close if _low is None else _low
            turnover = self._pick_float(row, "HG_HACIM", "HGDG_HACIM")
            average_price = self._pick_float(row, "HG_AOF", "HGDG_AOF")
            volume = (
                turnover / average_price
                if turnover is not None and average_price is not None and average_price > 0
                else None
            )

            bar_datetime = datetime.combine(
                bar_date,
                self.session.start,
                tzinfo=self.session.timezone,
            )

            bars.append(
                Bar(
                    symbol=symbol,
                    datetime=bar_datetime,
                    timeframe=TimeFrame.D1,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    is_closed=self._is_daily_bar_closed(bar_date),
                )
            )

        return sorted(bars, key=lambda bar: bar.datetime)

    def _is_daily_bar_closed(
        self,
        bar_date: date,
    ) -> bool:
        """Günlük barın kapalı kabul edilip edilmeyeceğini döndürür.

        Provider gecikmesi burada da intraday sınırlarında olduğu gibi
        uygulanır; böylece daily ve intraday kapanış kararları aynı güvenilirlik
        modelini kullanır.
        """
        delayed_now = datetime.now(self.session.timezone) - timedelta(minutes=self.delay_minutes)

        if bar_date < delayed_now.date():
            return True

        if bar_date > delayed_now.date():
            return False

        return delayed_now.time() >= self.session.end

    # ---- HTTP --------------------------------------------------------------

    async def _get_json(
        self,
        url: str,
        params: dict,
        symbol: str,
    ) -> dict:
        """İş Yatırım'dan JSON çeker ve hatalı payload'larda açıklayıcı hata üretir."""
        await self._rate_limiter.acquire()
        try:
            response = await self.client.get(
                url,
                params=params,
                headers={
                    "Referer": self._referer_url(symbol),
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        except httpx.HTTPError as exc:
            _log.error("HTTP isteği başarısız [%s]: %s", symbol, exc)
            raise

        if response.status_code == 429:
            _log.warning("Rate limit yanıtı [%s]: HTTP 429", symbol)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log.error("HTTP hata [%s]: %s %s", symbol, exc.response.status_code, exc.response.url)
            raise

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Invalid JSON response from IsYatirim. "
                f"status={response.status_code}, "
                f"url={response.url}, "
                f"text={response.text[:500]}"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected JSON payload type: {type(payload)}")

        return payload

    # ---- İş Yatırım helpers ------------------------------------------------

    @classmethod
    def _normalize_symbol(
        cls,
        symbol: str,
    ) -> str:
        """Kullanıcının verdiği ticker sembolünü normalize eder ve doğrular."""
        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol cannot be empty.")

        return symbol

    @classmethod
    def _referer_url(
        cls,
        symbol: str,
    ) -> str:
        """İş Yatırım endpoint'lerinin beklediği hisse kartı referer URL'ini oluşturur."""
        return (
            "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/"
            f"Sayfalar/sirket-karti.aspx?hisse={symbol}"
        )

    @classmethod
    def _parse_daily_date(
        cls,
        row: dict,
    ) -> date | None:
        """Bilinen İş Yatırım alan adları/formatlarından günlük satır tarihini parse eder."""
        value = row.get("HGDG_TARIH") or row.get("HG_TARIH")

        if not value:
            return None

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

        return None

    @classmethod
    def _pick_float(
        cls,
        row: dict,
        *keys: str,
    ) -> float | None:
        """Verilen satır anahtarlarından parse edilebilen ilk numeric değeri döndürür."""
        for key in keys:
            value = row.get(key)

            if value is None:
                continue

            try:
                if isinstance(value, str):
                    value = value.strip()
                    if "," in value:
                        value = value.replace(".", "").replace(",", ".")
                return float(value)
            except (TypeError, ValueError):
                continue

        return None
