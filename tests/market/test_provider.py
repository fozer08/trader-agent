from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_agent.market.cache import MarketBarCache
from trader_agent.market.provider import IsYatirimProvider
from trader_agent.market.types import Bar, IntradaySnapshot, PricePoint, TimeFrame, TradingSession


TZ = ZoneInfo("Europe/Istanbul")

SESSION = TradingSession(start=time(10, 0), end=time(18, 0), timezone=TZ)


class _CallCounter:
    """HTTP isteklerini URL substring'e göre sayar; cache davranış testleri için."""
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def hit(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def _provider(
    responses: dict[str, object],
    counter: _CallCounter | None = None,
) -> IsYatirimProvider:
    """Mock HTTP transport ile provider oluşturur. responses: {url_substring: json_body}"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, body in responses.items():
            if key in url:
                if counter is not None:
                    counter.hit(key)
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler=handler)
    return IsYatirimProvider(session=SESSION, _transport=transport)


def _empty_provider() -> IsYatirimProvider:
    return IsYatirimProvider(
        session=SESSION,
        _transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )


# ---- _normalize_symbol --------------------------------------------------------

def test_normalize_symbol_uppercases():
    assert IsYatirimProvider._normalize_symbol("thyao") == "THYAO"


def test_normalize_symbol_strips_whitespace():
    assert IsYatirimProvider._normalize_symbol("  GARAN  ") == "GARAN"


def test_normalize_symbol_raises_on_empty():
    with pytest.raises(ValueError):
        IsYatirimProvider._normalize_symbol("   ")


# ---- _parse_daily_date --------------------------------------------------------

def test_parse_daily_date_dd_mm_yyyy():
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": "29-04-2026"}) == date(2026, 4, 29)


def test_parse_daily_date_yyyy_mm_dd():
    assert IsYatirimProvider._parse_daily_date({"HG_TARIH": "2026-04-29"}) == date(2026, 4, 29)


def test_parse_daily_date_dd_dot_mm_dot_yyyy():
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": "29.04.2026"}) == date(2026, 4, 29)


def test_parse_daily_date_from_datetime_object():
    assert IsYatirimProvider._parse_daily_date(
        {"HGDG_TARIH": datetime(2026, 4, 29)}
    ) == date(2026, 4, 29)


def test_parse_daily_date_returns_none_on_missing():
    assert IsYatirimProvider._parse_daily_date({}) is None


def test_parse_daily_date_returns_none_on_bad_value():
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": "not-a-date"}) is None


# ---- _pick_float --------------------------------------------------------------

def test_pick_float_returns_first_valid():
    assert IsYatirimProvider._pick_float({"A": 10.5}, "A", "B") == 10.5


def test_pick_float_skips_none_and_tries_next():
    assert IsYatirimProvider._pick_float({"A": None, "B": "5.5"}, "A", "B") == 5.5


def test_pick_float_handles_turkish_decimal_format():
    assert IsYatirimProvider._pick_float({"A": "1.234,56"}, "A") == pytest.approx(1234.56)


def test_pick_float_returns_none_when_all_missing():
    assert IsYatirimProvider._pick_float({"A": None}, "A", "B") is None


def test_pick_float_returns_none_on_non_numeric():
    assert IsYatirimProvider._pick_float({"A": "abc"}, "A") is None


def test_provider_uses_injected_empty_cache():
    cache = MarketBarCache()
    provider = IsYatirimProvider(
        session=SESSION,
        cache=cache,
        _transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )

    assert provider.cache is cache


# ---- _daily_rows_to_bars ------------------------------------------------------

def test_daily_rows_to_bars_builds_correct_bar():
    rows = [{
        "HGDG_TARIH": "28-04-2026",
        "HGDG_ACILIS": "100.0",
        "HGDG_MAX": "105.0",
        "HGDG_MIN": "99.0",
        "HGDG_KAPANIS": "103.0",
        "HGDG_HACIM": "1030000.0",
        "HGDG_AOF": "103.0",
    }]
    provider = _empty_provider()
    bars = provider._daily_rows_to_bars("THYAO", rows)

    assert len(bars) == 1
    assert bars[0].open == 100.0
    assert bars[0].high == 105.0
    assert bars[0].low == 99.0
    assert bars[0].close == 103.0
    assert bars[0].volume == pytest.approx(10000.0)
    assert bars[0].timeframe is TimeFrame.D1


def test_daily_rows_to_bars_skips_row_without_close():
    rows = [{"HGDG_TARIH": "28-04-2026"}]
    provider = _empty_provider()
    assert provider._daily_rows_to_bars("THYAO", rows) == []


def test_price_points_to_bars_skips_unclosed_points():
    provider = _empty_provider()
    points = [
        PricePoint(datetime=datetime(2026, 4, 29, 10, 0, tzinfo=TZ), price=100.0),
        PricePoint(datetime=datetime(2026, 4, 29, 10, 1, tzinfo=TZ), price=101.0),
    ]

    bars = provider._price_points_to_bars(
        "THYAO",
        points,
        closed_until=datetime(2026, 4, 29, 10, 1, tzinfo=TZ),
    )

    assert [bar.datetime for bar in bars] == [points[0].datetime]


# ---- get_intraday / get_daily raises ------------------------------------------

@pytest.mark.asyncio
async def test_get_intraday_raises_on_d1():
    provider = _provider({})
    with pytest.raises(ValueError, match="get_daily"):
        await provider.get_intraday("THYAO", TimeFrame.D1)
    await provider.aclose()


@pytest.mark.asyncio
async def test_get_daily_raises_on_nonpositive_period():
    provider = _provider({})
    with pytest.raises(ValueError, match="positive"):
        await provider.get_daily("THYAO", period=0)
    await provider.aclose()


# ---- get_daily (mock HTTP) ----------------------------------------------------

@pytest.mark.asyncio
async def test_get_daily_returns_bars():
    body = {
        "ok": True,
        "value": [
            {
                "HGDG_TARIH": "28-04-2026",
                "HGDG_ACILIS": "100.0",
                "HGDG_MAX": "105.0",
                "HGDG_MIN": "99.0",
                "HGDG_KAPANIS": "103.0",
                "HGDG_HACIM": "1030000.0",
                "HGDG_AOF": "103.0",
            }
        ],
    }
    async with _provider({"HisseTekil": body}) as provider:
        bars = await provider.get_daily("THYAO", period=1)

    assert len(bars) == 1
    assert bars[0].close == 103.0
    assert bars[0].symbol == "THYAO"


# ---- Cache davranışı: verimli kullanım kanıtları ------------------------------

def _daily_body(rows: list[dict]) -> dict:
    return {"ok": True, "value": rows}


def _daily_row(date_str: str, close: float = 100.0) -> dict:
    return {
        "HGDG_TARIH": date_str,
        "HGDG_ACILIS": str(close),
        "HGDG_MAX": str(close),
        "HGDG_MIN": str(close),
        "HGDG_KAPANIS": str(close),
        "HGDG_HACIM": str(close * 1000),
        "HGDG_AOF": str(close),
    }


def _daily_rows_back(days: int, step: int = 1) -> list[dict]:
    today = datetime.now(TZ).date()
    return [
        _daily_row((today - timedelta(days=i)).strftime("%d-%m-%Y"))
        for i in range(0, days, step)
    ]


@pytest.mark.asyncio
async def test_get_daily_cache_hit_avoids_api():
    """İkinci çağrı cache'i kullanır, API'ye gitmez."""
    counter = _CallCounter()
    body = _daily_body(_daily_rows_back(days=365, step=7))

    async with _provider({"HisseTekil": body}, counter) as provider:
        await provider.get_daily("THYAO", period=5)
        api_calls_after_first = counter.counts.get("HisseTekil", 0)
        await provider.get_daily("THYAO", period=5)
        api_calls_after_second = counter.counts.get("HisseTekil", 0)

    # İkinci çağrı API'ye gitmemeli
    assert api_calls_after_second == api_calls_after_first


@pytest.mark.asyncio
async def test_get_daily_concurrent_calls_dedupe_fetch():
    """Aynı anda 5 çağrı: lock tek fetch garantiler."""
    counter = _CallCounter()
    body = _daily_body(_daily_rows_back(days=365, step=7))

    async with _provider({"HisseTekil": body}, counter) as provider:
        results = await asyncio.gather(*[provider.get_daily("THYAO", period=3) for _ in range(5)])

    assert counter.counts.get("HisseTekil", 0) == 1
    # Hepsi aynı sonucu almalı
    assert all(len(r) == 3 for r in results)


@pytest.mark.asyncio
async def test_get_daily_cross_symbol_isolation():
    """Bir sembol cache'liyken farklı sembol kendi fetch'ini tetikler."""
    counter = _CallCounter()
    body = _daily_body(_daily_rows_back(days=365, step=7))

    async with _provider({"HisseTekil": body}, counter) as provider:
        await provider.get_daily("THYAO", period=1)
        await provider.get_daily("GARAN", period=1)

    # İki farklı sembol için iki ayrı fetch
    assert counter.counts.get("HisseTekil", 0) == 2


@pytest.mark.asyncio
async def test_get_daily_large_period_expands_fetch_window():
    """Büyük period istekleri varsayılan 365 günlük pencereyle sınırlı kalmaz."""
    requested_start_dates: list[date] = []
    today = datetime.now(TZ).date()
    body = _daily_body(_daily_rows_back(days=450))

    def handler(request: httpx.Request) -> httpx.Response:
        requested_start_dates.append(
            datetime.strptime(request.url.params["startdate"], "%d-%m-%Y").date()
        )
        return httpx.Response(200, json=body)

    async with IsYatirimProvider(
        session=SESSION,
        _transport=httpx.MockTransport(handler),
    ) as provider:
        result = await provider.get_daily("THYAO", period=300)

    assert len(result) == 300
    assert requested_start_dates
    assert requested_start_dates[0] <= today - timedelta(days=440)


# ---- Intraday cache davranışı --------------------------------------------------

def _intraday_body(timestamps_ms: list[int], price: float = 100.0) -> dict:
    return {"data": [[ts, price] for ts in timestamps_ms]}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.mark.asyncio
async def test_get_intraday_cache_hit_avoids_api():
    """Pencere dolmadan ikinci çağrı: cache hit, API'ye gitmez."""
    counter = _CallCounter()
    now = datetime.now(TZ)
    bounds_end = now - timedelta(minutes=IsYatirimProvider.delay_minutes)
    session_start = datetime.combine(bounds_end.date(), SESSION.start, tzinfo=TZ)
    if bounds_end.weekday() >= 5 or bounds_end < session_start:
        pytest.skip("Hafta sonu veya seans öncesi, mock anlamlı çalışmaz")

    minute_dts = []
    cur = session_start
    # cur <= bounds_end: mevcut dakikayı (açık bar) da dahil et
    while cur <= bounds_end:
        minute_dts.append(cur)
        cur += timedelta(minutes=1)
    body = _intraday_body([_ms(dt) for dt in minute_dts])

    async with _provider({"ChartData": body}, counter) as provider:
        await provider.get_intraday("THYAO", TimeFrame.M1)
        first_count = counter.counts.get("ChartData", 0)
        await provider.get_intraday("THYAO", TimeFrame.M1)
        second_count = counter.counts.get("ChartData", 0)

    # Pencere içinde ikinci çağrı yeni veri çekmez (son bar kapalı sayılır)
    assert second_count == first_count


@pytest.mark.asyncio
async def test_get_intraday_concurrent_calls_dedupe_fetch():
    """Aynı anda gelen intraday çağrıları lock ile dedupe edilir."""
    counter = _CallCounter()
    now = datetime.now(TZ)
    bounds_end = now - timedelta(minutes=IsYatirimProvider.delay_minutes)
    session_start = datetime.combine(bounds_end.date(), SESSION.start, tzinfo=TZ)
    if bounds_end.weekday() >= 5 or bounds_end < session_start:
        pytest.skip("Hafta sonu veya seans öncesi")

    minute_dts = []
    cur = session_start
    # cur <= bounds_end: mevcut dakikayı (açık bar) da dahil et
    while cur <= bounds_end:
        minute_dts.append(cur)
        cur += timedelta(minutes=1)
    body = _intraday_body([_ms(dt) for dt in minute_dts])

    async with _provider({"ChartData": body}, counter) as provider:
        await asyncio.gather(*[provider.get_intraday("THYAO", TimeFrame.M1) for _ in range(5)])

    assert counter.counts.get("ChartData", 0) == 1


@pytest.mark.asyncio
async def test_get_intraday_failed_attempt_is_not_recorded():
    """HTTP hatası attempt sayılmaz; sonraki çağrı yeniden dener."""
    counter = _CallCounter()
    now = datetime.now(TZ)
    bounds_end = now - timedelta(minutes=IsYatirimProvider.delay_minutes)
    session_start = datetime.combine(bounds_end.date(), SESSION.start, tzinfo=TZ)
    if bounds_end.weekday() >= 5 or bounds_end < session_start:
        pytest.skip("Hafta sonu veya seans öncesi")

    def handler(request: httpx.Request) -> httpx.Response:
        counter.hit("ChartData")
        return httpx.Response(500, json={"error": "boom"})

    async with IsYatirimProvider(
        session=SESSION,
        _transport=httpx.MockTransport(handler),
    ) as provider:
        with pytest.raises(httpx.HTTPStatusError):
            await provider.get_intraday("THYAO", TimeFrame.M1)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.get_intraday("THYAO", TimeFrame.M1)

    assert counter.counts.get("ChartData", 0) == 2


@pytest.mark.asyncio
async def test_get_today_returns_snapshot():
    """get_today intraday'den günlük snapshot üretir."""
    counter = _CallCounter()
    now = datetime.now(TZ)
    bounds_end = now - timedelta(minutes=IsYatirimProvider.delay_minutes)
    session_start = datetime.combine(bounds_end.date(), SESSION.start, tzinfo=TZ)
    if bounds_end.weekday() >= 5 or bounds_end < session_start:
        pytest.skip("Hafta sonu veya seans öncesi")

    # 3 fiyat noktası: 100, 110 (high), 95 (low) — close = 95
    pts = [
        session_start,
        session_start + timedelta(minutes=1),
        session_start + timedelta(minutes=2),
    ]
    body = {"data": [
        [_ms(pts[0]), 100.0],
        [_ms(pts[1]), 110.0],
        [_ms(pts[2]), 95.0],
    ]}

    async with _provider({"ChartData": body}, counter) as provider:
        snapshot = await provider.get_today("THYAO")

    assert snapshot is not None
    assert isinstance(snapshot, IntradaySnapshot)
    assert snapshot.symbol == "THYAO"
    assert snapshot.open == 100.0
    assert snapshot.high == 110.0
    assert snapshot.low == 95.0
    assert snapshot.close == 95.0
