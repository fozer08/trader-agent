from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from ..utils.logging import get_logger
from .base import MarketDataProvider, PricePoint, TradingSession
from .cache import MarketBarCache
from .helpers import aggregate_bars, calc_session_bounds, floor_to_timeframe, ohlcv_from_bars
from .rate_limiter import RateLimiter
from ..types import Bar, IntradaySnapshot, TimeFrame

_log = get_logger(__name__)


class IsYatirimProvider(MarketDataProvider):
    """İş Yatırım'ın public chart endpoint'lerinden BIST verisi çeken async provider.

    Gün içi veri noktasal fiyat olarak gelir, M1 barlarına normalize edilir.
    Günlük veri ayrı historical endpoint'ten gelir.
    """

    source = "isyatirim"
    supported_exchanges = {"bist"}
    calls_per_minute = 15

    delay_minutes = 15   # İş Yatırım ücretsiz feed gecikmesi (dakika)
    user_agent = "Mozilla/5.0"

    source_tf = TimeFrame.M1          # API'nin desteklediği en küçük çözünürlük
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
        session: TradingSession,
        timeout: int = 10,
        cache: MarketBarCache | None = None,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.session = session
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=timeout,
            transport=_transport,
        )
        self.cache = cache if cache is not None else MarketBarCache()
        self._rate_limiter = RateLimiter(self.calls_per_minute)
        self._fetch_locks: dict[tuple[str, TimeFrame], asyncio.Lock] = {}

    async def __aenter__(self) -> IsYatirimProvider:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    def _get_lock(self, key: tuple[str, TimeFrame]) -> asyncio.Lock:
        """Sembol/timeframe için lock döndürür, yoksa oluşturur."""
        lock = self._fetch_locks.get(key)
        if lock is None:
            lock = self._fetch_locks[key] = asyncio.Lock()
        return lock

    # ---- Public API --------------------------------------------------------

    async def get_intraday(self, symbol: str, tf: TimeFrame) -> list[Bar]:
        """Bugünün barlarını istenen timeframe'e aggregate ederek döndürür.

        Sadece feed gecikmesi düşüldükten sonra güvenilir sayılan aralığı verir.
        Günlük bar için get_daily kullanılmalı.
        """
        if tf is TimeFrame.D1:
            raise ValueError("Use get_daily() for daily bars.")

        symbol = self._normalize_symbol(symbol)
        bounds = self._session_bounds()
        if bounds is None:
            return []
        start, end = bounds
        source_bars = await self._get_intraday_bars(symbol, start, end)
        return aggregate_bars(
            bars=source_bars,
            target_tf=tf,
            closed_until=self._delayed_now(),
            session_start=self.session.start,
        )

    async def get_today(self, symbol: str) -> IntradaySnapshot | None:
        """Bugünün intraday verisinden derlenen günlük snapshot'ı döndürür.

        Daily endpoint'ten değil, intraday'den üretildiği için volume=None olabilir.
        """
        symbol = self._normalize_symbol(symbol)
        bounds = self._session_bounds()
        if bounds is None:
            return None
        start, end = bounds
        source_bars = await self._get_intraday_bars(symbol, start, end)
        if not source_bars:
            return None

        open_, high, low, close, volume = ohlcv_from_bars(source_bars)
        return IntradaySnapshot(
            symbol=symbol,
            datetime=source_bars[-1].datetime,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    async def get_daily(self, symbol: str, period: int) -> list[Bar]:
        """Son `period` adet günlük barı döndürür; cache hit'lerde API'ye gitmez."""
        if period <= 0:
            raise ValueError("period must be positive.")

        symbol = self._normalize_symbol(symbol)
        await self._fetch_daily_if_needed(symbol, period)
        return self.cache.last_n(symbol, TimeFrame.D1, period)

    async def _fetch_daily_if_needed(self, symbol: str, period: int) -> None:
        start, end = self._daily_fetch_range(period)
        if not self._daily_cache_satisfies(symbol, period, end):
            async with self._get_lock((symbol, TimeFrame.D1)):
                if self._daily_cache_satisfies(symbol, period, end):
                    return
                rows = await self._fetch_daily_rows(symbol, start.date(), end.date())
                self.cache.set(symbol, TimeFrame.D1, self._daily_rows_to_bars(symbol, rows))

    def _daily_cache_satisfies(self, symbol: str, period: int, expected_end: datetime) -> bool:
        cached = self.cache.last_n(symbol, TimeFrame.D1, period)
        return len(cached) >= period and cached[-1].datetime >= expected_end

    def _daily_fetch_range(self, period: int) -> tuple[datetime, datetime]:
        """API'den çekilecek (start, end) aralığı; period için yeterli takvim günü içerir."""
        today = datetime.now(self.session.timezone).date()
        days = max(
            self._daily_fetch_days,
            int(period * 7 / 5) + self._daily_fetch_padding_days,
        )
        start = self._weekday_dt(today - timedelta(days=days), forward=True)
        return start, self._last_completed_daily_dt()

    def _session_bounds(self) -> tuple[datetime, datetime] | None:
        """Şu an için güvenilir kabul edilen seans aralığını döndürür."""
        return calc_session_bounds(
            now=datetime.now(self.session.timezone),
            session_start=self.session.start,
            session_end=self.session.end,
            delay_minutes=self.delay_minutes,
        )

    def _delayed_now(self) -> datetime:
        """Feed gecikmesini düşülmüş wall-clock zamanını döndürür."""
        return datetime.now(self.session.timezone) - timedelta(minutes=self.delay_minutes)

    def _weekday_dt(self, ref: date, *, forward: bool) -> datetime:
        """Tarihi en yakın haftaiçine kaydırır ve seans başı ile birleştirir."""
        # forward=True: cumartesi/pazar olursa pazartesiye atlar; False ise cumaya geri çekilir
        step = timedelta(days=1 if forward else -1)
        while ref.weekday() >= 5:
            ref += step
        return datetime.combine(ref, self.session.start, tzinfo=self.session.timezone)

    def _last_completed_daily_dt(self) -> datetime:
        """Beklenen son tamamlanmış daily bar'ın datetime'ı.

        Bugünün daily barı ancak feed gecikmesiyle birlikte seans bitişi
        görüldükten sonra tamamlanmış sayılır.
        """
        delayed_now = self._delayed_now()
        ref = delayed_now.date()
        if delayed_now.time() < self.session.end:
            ref -= timedelta(days=1)
        return self._weekday_dt(ref, forward=False)

    # ---- Fetch -------------------------------------------------------------

    async def _get_intraday_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """[start, end] aralığındaki source_tf barlarını döndürür; eksikse API'den çeker."""
        # Cache yalnızca kapanmış barları tuttuğu için aranan üst sınır son kapalı bar başlangıcı
        cache_end = floor_to_timeframe(
            end - timedelta(minutes=self.source_tf.minutes),
            self.source_tf,
            self.session.start,
        )
        if cache_end < start:
            return []

        # Lock: aynı sembol/tf için eş zamanlı çağrılarda tek fetch garantilenir
        async with self._get_lock((symbol, self.source_tf)):
            missing = self.cache.missing(symbol, self.source_tf, start, cache_end)
            if missing is not None:
                points = await self._fetch_intraday_points(symbol, missing[0], end)
                bars = self._price_points_to_bars(symbol, points, end)
                self.cache.set(symbol, self.source_tf, bars)

        last = self.cache.last(symbol, self.source_tf)
        if last is None or last.datetime < start:
            return []
        return self.cache.get(symbol, self.source_tf, start, last.datetime) or []

    async def _fetch_intraday_points(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PricePoint]:
        """Chart endpoint'inden ham fiyat noktalarını çeker."""
        params = {
            "period": self.source_tf.minutes,
            "from": start.strftime("%Y%m%d%H%M%S"),
            "to": end.strftime("%Y%m%d%H%M%S"),
            "endeks": symbol,
        }

        payload = await self._get_json(self.intraday_url, params, symbol)
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

            # API epoch millisaniye döndürür; UTC'ye çevir, sonra seans timezone'una al
            dt = datetime.fromtimestamp(
                float(timestamp_ms) / 1000,
                timezone.utc,
            ).astimezone(self.session.timezone)

            points.append(PricePoint(datetime=dt, price=float(price)))

        return sorted(points, key=lambda p: p.datetime)

    def _price_points_to_bars(
        self,
        symbol: str,
        points: list[PricePoint],
        closed_until: datetime,
    ) -> list[Bar]:
        """Kapanmış fiyat noktalarını M1 barlarına çevirir (O=H=L=C, volume yok)."""
        window = timedelta(minutes=self.source_tf.minutes)
        return [
            Bar(
                symbol=symbol,
                datetime=p.datetime,
                timeframe=self.source_tf,
                open=p.price,
                high=p.price,
                low=p.price,
                close=p.price,
                volume=None,
            )
            for p in points
            if p.datetime + window <= closed_until
        ]

    async def _fetch_daily_rows(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[dict]:
        """Historical endpoint'ten ham günlük satırları çeker."""
        params = {
            "hisse": symbol,
            "startdate": start.strftime("%d-%m-%Y"),
            "enddate": end.strftime("%d-%m-%Y"),
        }
        payload = await self._get_json(self.historical_url, params, symbol)

        if not payload.get("ok"):
            raise RuntimeError(f"IsYatirim daily response is not ok: {payload}")

        rows = payload.get("value", [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected daily payload type: {type(rows)}")
        return rows

    # ---- Convert -----------------------------------------------------------

    def _daily_rows_to_bars(self, symbol: str, rows: list[dict]) -> list[Bar]:
        """Ham günlük satırları normalize D1 Bar'larına çevirir."""
        bars: list[Bar] = []

        for row in rows:
            bar_date = self._parse_daily_date(row)
            if bar_date is None:
                continue

            # Close zorunlu; yoksa bar'ı atla
            close = self._pick_float(row, "HG_KAPANIS", "HGDG_KAPANIS")
            if close is None:
                continue

            raw_open = self._pick_float(row, "HG_ACILIS", "HGDG_ACILIS")
            raw_high = self._pick_float(row, "HG_MAX", "HGDG_MAX")
            raw_low = self._pick_float(row, "HG_MIN", "HGDG_MIN")

            # API doğrudan volume vermez; ciro (TL) / ağırlıklı ortalama fiyat = lot adedi
            turnover = self._pick_float(row, "HG_HACIM", "HGDG_HACIM")
            avg_price = self._pick_float(row, "HG_AOF", "HGDG_AOF")
            volume = (
                turnover / avg_price
                if turnover is not None and avg_price is not None and avg_price > 0
                else None
            )

            bars.append(
                Bar(
                    symbol=symbol,
                    datetime=datetime.combine(
                        bar_date,
                        self.session.start,
                        tzinfo=self.session.timezone,
                    ),
                    timeframe=TimeFrame.D1,
                    open=close if raw_open is None else raw_open,
                    high=close if raw_high is None else raw_high,
                    low=close if raw_low is None else raw_low,
                    close=close,
                    volume=volume,
                )
            )

        return sorted(bars, key=lambda b: b.datetime)

    # ---- HTTP --------------------------------------------------------------

    async def _get_json(self, url: str, params: dict, symbol: str) -> dict:
        """Endpoint'ten JSON çeker, bozuk yanıtta açıklayıcı hata fırlatır."""
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
        except httpx.HTTPError as exc:
            _log.error("HTTP isteği başarısız [%s]: %s", symbol, exc)
            raise

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
    def _parse_daily_date(row: dict) -> date | None:
        """Daily satırından bilinen alan adları ve formatlardan tarihi okur."""
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

    @staticmethod
    def _pick_float(row: dict, *keys: str) -> float | None:
        """Verilen anahtarlardan float'a çevrilebilen ilk değeri döndürür."""
        # Birden fazla key denenir çünkü API bazen HG_, bazen HGDG_ prefix'i kullanır
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
