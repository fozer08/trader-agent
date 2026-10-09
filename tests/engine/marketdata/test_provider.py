from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.marketdata.base import ProviderResponseError
from trader_agent.engine.calendar import ExchangeSchedule, TradingCalendar
from trader_agent.engine.marketdata.provider import IsYatirimProvider, _PricePoint
from trader_agent.engine.marketdata.service import MarketService
from trader_agent.engine.marketdata.types import SessionSnapshot, TimeFrame

TZ = ZoneInfo("Europe/Istanbul")
CALENDAR = TradingCalendar(ExchangeSchedule(time(10), time(18), TZ), {}, frozenset({2026}))


# ---- helpers -----------------------------------------------------------------

class _CallCounter:
    """HTTP isteklerini URL substring'e göre sayar."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def hit(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def _provider(
    responses: dict[str, object],
    counter: _CallCounter | None = None,
    *,
    store: MarketBarCache | None = None,
) -> IsYatirimProvider:
    """Mock HTTP transport ile İş Yatırım sağlayıcısı."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, body in responses.items():
            if key in url:
                if counter is not None:
                    counter.hit(key)
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler=handler)
    return IsYatirimProvider(calendar=CALENDAR, _transport=transport, store=store)


def _empty_provider() -> IsYatirimProvider:
    return IsYatirimProvider(
        calendar=CALENDAR,
        _transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )


def _intraday_body(timestamps: list[datetime], price: float = 100.0) -> dict:
    return {"data": [[_ms(dt), price] for dt in timestamps]}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture
def intraday_window():
    """Gün içi testlerini çalıştırma saatinden bağımsız tutar."""
    start = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = start + timedelta(minutes=75)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    with (
        patch("trader_agent.engine.marketdata.provider.datetime", new=FixedDatetime),
        patch("trader_agent.engine.marketdata.service.datetime", new=FixedDatetime),
    ):
        yield [start + timedelta(minutes=i) for i in range(61)]


# ---- _normalize_symbol --------------------------------------------------------

def test_normalize_symbol_uppercases():
    assert IsYatirimProvider._normalize_symbol("thyao") == "THYAO"


def test_normalize_symbol_strips_whitespace():
    assert IsYatirimProvider._normalize_symbol("  THYAO  ") == "THYAO"


def test_normalize_symbol_raises_on_empty():
    with pytest.raises(ValueError, match="empty"):
        IsYatirimProvider._normalize_symbol("   ")


# ---- _parse_daily_date --------------------------------------------------------

def test_parse_daily_date_dd_mm_yyyy():
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": "28-04-2026"}) == date(2026, 4, 28)


def test_parse_daily_date_yyyy_mm_dd():
    assert IsYatirimProvider._parse_daily_date({"HG_TARIH": "2026-04-28"}) == date(2026, 4, 28)


def test_parse_daily_date_dd_dot_mm_dot_yyyy():
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": "28.04.2026"}) == date(2026, 4, 28)


def test_parse_daily_date_from_datetime_object():
    dt = datetime(2026, 4, 28, 10, 30)
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": dt}) == date(2026, 4, 28)


def test_parse_daily_date_rejects_missing():
    with pytest.raises(ProviderResponseError, match="date"):
        IsYatirimProvider._parse_daily_date({})


def test_parse_daily_date_rejects_bad_value():
    with pytest.raises(ProviderResponseError, match="date"):
        IsYatirimProvider._parse_daily_date({"HGDG_TARIH": "not-a-date"})


# ---- _pick_float --------------------------------------------------------------

def test_pick_float_returns_first_valid():
    assert IsYatirimProvider._pick_float({"A": 10.5}, "A", "B") == 10.5


def test_pick_float_skips_none_and_tries_next():
    assert IsYatirimProvider._pick_float({"A": None, "B": "5.5"}, "A", "B") == 5.5


def test_pick_float_handles_turkish_decimal_format():
    assert IsYatirimProvider._pick_float({"A": "1.234,56"}, "A") == pytest.approx(1234.56)


def test_pick_float_returns_none_when_all_missing():
    assert IsYatirimProvider._pick_float({"A": None}, "A", "B") is None


def test_pick_float_rejects_non_numeric():
    with pytest.raises(ProviderResponseError, match="A"):
        IsYatirimProvider._pick_float({"A": "abc"}, "A")


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

    assert len(bars.bars) == 1
    assert bars.bars[0].open == 100.0
    assert bars.bars[0].high == 105.0
    assert bars.bars[0].low == 99.0
    assert bars.bars[0].close == 103.0
    assert bars.bars[0].volume == pytest.approx(10000.0)
    assert bars.timeframe is TimeFrame.D1


def test_daily_rows_to_bars_rejects_row_without_close():
    rows = [{"HGDG_TARIH": "28-04-2026"}]
    provider = _empty_provider()
    with pytest.raises(ProviderResponseError, match="close"):
        provider._daily_rows_to_bars("THYAO", rows)


def test_price_points_to_bars_skips_unclosed_points():
    provider = _empty_provider()
    points = [
        _PricePoint(datetime=datetime(2026, 4, 29, 10, 0, tzinfo=TZ), price=100.0),
        _PricePoint(datetime=datetime(2026, 4, 29, 10, 1, tzinfo=TZ), price=101.0),
    ]

    bars = provider._price_points_to_bars(
        "THYAO",
        points,
        closed_until=datetime(2026, 4, 29, 10, 1, tzinfo=TZ),
    )

    assert [bar.period_start for bar in bars.bars] == [points[0].datetime]


# ---- Daily fetch — no cache, direct API every call ---------------------------


@pytest.mark.asyncio
async def test_get_daily_raises_on_nonpositive_period():
    provider = _provider({})
    with pytest.raises(ValueError, match="positive"):
        await provider.get_daily("THYAO", period=0)
    await provider.aclose()


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

    assert len(bars.bars) == 1
    assert bars.bars[0].close == 103.0
    assert bars.symbol == "THYAO"


@pytest.mark.asyncio
async def test_get_daily_each_call_hits_api():
    """Günlük önbellek servistedir; doğrudan get_daily çağrısı upstream'e gider."""
    counter = _CallCounter()
    body = {"ok": True, "value": _rows_back(365, step=7)}

    async with _provider({"HisseTekil": body}, counter) as provider:
        await provider.get_daily("THYAO", period=5)
        await provider.get_daily("THYAO", period=5)

    assert counter.counts.get("HisseTekil", 0) == 2


@pytest.mark.asyncio
async def test_get_daily_large_period_expands_fetch_window():
    """Büyük period: fetch geriye period * 7/5 + padding kadar uzar."""
    requested_start_dates: list[date] = []
    today = datetime.now(TZ).date()
    body = {"ok": True, "value": _rows_back(450)}

    def handler(request: httpx.Request) -> httpx.Response:
        requested_start_dates.append(
            datetime.strptime(request.url.params["startdate"], "%d-%m-%Y").date()
        )
        return httpx.Response(200, json=body)

    async with IsYatirimProvider(
        calendar=CALENDAR,
        _transport=httpx.MockTransport(handler),
    ) as provider:
        result = await provider.get_daily("THYAO", period=300)

    # period=300 → int(300 * 7/5) + 10 = 430 takvim günü geri git
    assert len(result.bars) == 300
    assert requested_start_dates
    assert requested_start_dates[0] <= today - timedelta(days=400)


@pytest.mark.asyncio
async def test_daily_fetch_range_keeps_initial_time_across_close_boundary():
    """Tarih aralığı hesaplanırken 18:15 sınırının geçilmesi sonucu değiştirmesin."""
    async with _provider({}) as provider:
        with patch("trader_agent.engine.marketdata.provider.datetime", wraps=datetime) as clock:
            clock.now.side_effect = [
                datetime(2026, 10, 7, 18, 14, 59, tzinfo=TZ),
                datetime(2026, 10, 7, 18, 15, tzinfo=TZ),
            ]
            _, end = provider._daily_fetch_range(100)

    assert end == datetime(2026, 10, 6, 10, 0, tzinfo=TZ)
    assert clock.now.call_count == 1


# ---- Provider-owned intraday accumulation ------------------------------------


@pytest.mark.asyncio
async def test_get_intraday_raises_on_d1():
    async with _provider({}) as provider:
        with pytest.raises(ValueError, match="get_daily"):
            await provider.get_intraday("THYAO", TimeFrame.D1)


@pytest.mark.asyncio
async def test_get_intraday_does_not_close_bucket_while_fetching():
    """HTTP sırasında zaman ilerlese de başlangıçtaki kapanış sınırı kullanılsın."""
    start = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
    body = _intraday_body([start + timedelta(minutes=i) for i in range(5)])

    async with _provider({"ChartData": body}) as provider:
        with patch("trader_agent.engine.marketdata.provider.datetime", wraps=datetime) as clock:
            clock.now.side_effect = [
                start + timedelta(minutes=19),
                start + timedelta(minutes=20),
            ]
            bars = await provider.get_intraday("THYAO", TimeFrame.M5)

    assert bars.bars == []
    assert bars.symbol == "THYAO"
    assert bars.timeframe is TimeFrame.M5
    assert clock.now.call_count == 1


@pytest.mark.asyncio
async def test_get_intraday_cache_hit_avoids_api(intraday_window):
    counter = _CallCounter()
    body = _intraday_body(intraday_window)

    async with _provider({"ChartData": body}, counter) as provider:
        first = await provider.get_intraday("THYAO", TimeFrame.M1)
        second = await provider.get_intraday("THYAO", TimeFrame.M1)

    assert len(first.bars) == 60
    assert second == first
    assert counter.counts["ChartData"] == 1


@pytest.mark.asyncio
async def test_get_intraday_concurrent_calls_dedupe_fetch(intraday_window):
    counter = _CallCounter()
    body = _intraday_body(intraday_window)

    async with _provider({"ChartData": body}, counter) as provider:
        results = await asyncio.gather(
            *[provider.get_intraday("THYAO", TimeFrame.M1) for _ in range(5)]
        )

    assert all(len(bars.bars) == 60 for bars in results)
    assert counter.counts["ChartData"] == 1


async def test_missing_open_preserves_remaining_bars_and_snapshot(intraday_window):
    counter = _CallCounter()
    points = intraday_window[1:]
    async with _provider({"ChartData": _intraday_body(points)}, counter) as provider:
        bars = await provider.get_intraday("THYAO", TimeFrame.M1)
        snapshot = await provider.get_today("THYAO")
        again = await provider.get_intraday("THYAO", TimeFrame.M1)
    assert [bar.period_start for bar in bars.bars] == points[:-1]
    assert again == bars
    assert snapshot is not None and snapshot.ohlcv.close == 100
    assert snapshot.as_of == points[-2]
    assert counter.counts["ChartData"] == 1


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", -5, 0, True, "bad"])
async def test_invalid_intraday_price_is_rejected(intraday_window, price):
    body = {"data": [[_ms(intraday_window[0]), price]]}
    async with _provider({"ChartData": body}) as provider:
        with pytest.raises(ProviderResponseError):
            await provider.get_intraday("THYAO", TimeFrame.M1)


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", -5, 0, True, "bad"])
async def test_invalid_daily_price_is_rejected(intraday_window, price):
    body = {"ok": True, "value": [{"HGDG_TARIH": "06-10-2026", "HGDG_KAPANIS": price}]}
    async with _provider({"HisseTekil": body}) as provider:
        with pytest.raises(ProviderResponseError):
            await provider.get_daily("THYAO", 1)


@pytest.mark.asyncio
async def test_intraday_timeframes_and_snapshot_share_source_cache(intraday_window):
    counter = _CallCounter()
    body = {"data": [[_ms(dt), 100.0 + i] for i, dt in enumerate(intraday_window)]}

    async with _provider({"ChartData": body}, counter) as provider:
        m5, m15, snapshot = await asyncio.gather(
            provider.get_intraday(" thyao ", TimeFrame.M5),
            provider.get_intraday("THYAO", TimeFrame.M15),
            provider.get_today("THYAO"),
        )

    assert len(m5.bars) == 12
    assert len(m15.bars) == 4
    assert m5.timeframe is TimeFrame.M5
    assert m15.timeframe is TimeFrame.M15
    assert m5.symbol == m15.symbol == "THYAO"
    assert (m5.bars[0].open, m5.bars[0].high, m5.bars[0].low, m5.bars[0].close) == (100, 104, 100, 104)
    assert (m15.bars[0].open, m15.bars[0].high, m15.bars[0].low, m15.bars[0].close) == (100, 114, 100, 114)
    assert snapshot is not None
    assert (snapshot.ohlcv.open, snapshot.ohlcv.high, snapshot.ohlcv.low, snapshot.ohlcv.close) == (100, 159, 100, 159)
    assert snapshot.ohlcv.volume is None
    assert counter.counts["ChartData"] == 1


@pytest.mark.asyncio
async def test_get_intraday_fetches_only_missing_tail(monkeypatch):
    start = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
    end = start + timedelta(minutes=60)
    requests: list[httpx.Request] = []
    body = _intraday_body([start + timedelta(minutes=i) for i in range(63)])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    async with IsYatirimProvider(
        calendar=CALENDAR,
        _transport=httpx.MockTransport(handler),
    ) as provider:
        monkeypatch.setattr(provider, "_session_bounds", lambda: (start, end))
        first = await provider.get_intraday("THYAO", TimeFrame.M1)
        end += timedelta(minutes=2)
        second = await provider.get_intraday("THYAO", TimeFrame.M1)

    assert len(first.bars) == 60
    assert len(second.bars) == 62
    assert len(requests) == 2
    assert requests[1].url.params["from"] == "20261007110000"
    assert requests[1].url.params["to"] == "20261007110200"
    assert requests[1].url.params["period"] == "1"


@pytest.mark.asyncio
async def test_get_intraday_failed_attempt_is_not_recorded(intraday_window):
    counter = _CallCounter()

    def handler(request: httpx.Request) -> httpx.Response:
        counter.hit("ChartData")
        return httpx.Response(500, json={"error": "boom"})

    async with IsYatirimProvider(
        calendar=CALENDAR,
        _transport=httpx.MockTransport(handler),
    ) as provider:
        with pytest.raises(ProviderResponseError):
            await provider.get_intraday("THYAO", TimeFrame.M1)
        with pytest.raises(ProviderResponseError):
            await provider.get_intraday("THYAO", TimeFrame.M1)

    assert counter.counts["ChartData"] == 2


@pytest.mark.asyncio
async def test_get_today_returns_cached_snapshot(monkeypatch):
    start = datetime(2026, 10, 6, 10, 0, tzinfo=TZ)
    end = start + timedelta(minutes=3)
    body = {"data": [
        [_ms(start), 100.0],
        [_ms(start + timedelta(minutes=1)), 110.0],
        [_ms(start + timedelta(minutes=2)), 95.0],
    ]}
    counter = _CallCounter()

    async with _provider({"ChartData": body}, counter) as provider:
        monkeypatch.setattr(provider, "_session_bounds", lambda: (start, end))
        snapshot = await provider.get_today("THYAO")
        cached = await provider.get_today("THYAO")

    assert isinstance(snapshot, SessionSnapshot)
    assert snapshot.symbol == "THYAO"
    assert snapshot.ohlcv.period_start == start
    assert snapshot.as_of == start + timedelta(minutes=2)
    assert snapshot.ohlcv.open == 100.0
    assert snapshot.ohlcv.high == 110.0
    assert snapshot.ohlcv.low == 95.0
    assert snapshot.ohlcv.close == 95.0
    assert snapshot.ohlcv.volume is None
    assert cached == snapshot
    assert counter.counts["ChartData"] == 1


@pytest.mark.asyncio
async def test_provider_and_service_use_separate_buckets_in_shared_store(
    intraday_window, monkeypatch,
):
    counter = _CallCounter()
    bar_store = MarketBarCache()
    daily = {"ok": True, "value": [{
        "HGDG_TARIH": "06-10-2026",
        "HGDG_ACILIS": "100.0",
        "HGDG_MAX": "110.0",
        "HGDG_MIN": "95.0",
        "HGDG_KAPANIS": "105.0",
    }]}
    intraday = _intraday_body(intraday_window)

    async with _provider(
        {"HisseTekil": daily, "ChartData": intraday}, counter, store=bar_store,
    ) as provider:
        monkeypatch.setattr(provider, "_daily_fetch_range", lambda period: (
            datetime(2026, 10, 1, 10, 0, tzinfo=TZ),
            datetime(2026, 10, 6, 10, 0, tzinfo=TZ),
        ))
        service = MarketService(provider, store=bar_store)
        daily_bars = await service.get_daily("THYAO", period=1)
        intraday_bars = await service.get_intraday("THYAO", TimeFrame.M5)
        await service.get_daily("THYAO", period=1)
        snapshot = await service.get_today("THYAO")

    assert len(daily_bars.bars) == 1
    assert len(intraday_bars.bars) == 12
    assert snapshot is not None
    assert len(bar_store.last_n("THYAO", TimeFrame.M1, 100).bars) == 60
    assert bar_store.last_n("THYAO", TimeFrame.D1, 1) == daily_bars
    assert counter.counts == {"HisseTekil": 1, "ChartData": 1}


# ---- helpers (continued) -----------------------------------------------------

def _rows_back(days: int, step: int = 1) -> list[dict]:
    """Bugünden geriye `days` gün boyunca step ile bar satırları üretir."""
    today = datetime.now(TZ).date()
    return [
        {
            "HGDG_TARIH": (today - timedelta(days=i)).strftime("%d-%m-%Y"),
            "HGDG_ACILIS": "100.0",
            "HGDG_MAX": "100.0",
            "HGDG_MIN": "100.0",
            "HGDG_KAPANIS": "100.0",
            "HGDG_HACIM": "100000.0",
            "HGDG_AOF": "100.0",
        }
        for i in range(0, days, step)
    ]
