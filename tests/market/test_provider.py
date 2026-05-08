from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_agent.market.provider import IsYatirimProvider
from trader_agent.market.types import TimeFrame, TradingSession


TZ = ZoneInfo("Europe/Istanbul")

SESSION = TradingSession(start=time(10, 0), end=time(18, 0), timezone=TZ)


def _provider(responses: dict[str, object]) -> IsYatirimProvider:
    """Mock HTTP transport ile provider oluşturur. responses: {url_substring: json_body}"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, body in responses.items():
            if key in url:
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler=handler)
    return IsYatirimProvider(session=SESSION, _transport=transport)


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
    assert IsYatirimProvider._parse_daily_date({"HGDG_TARIH": datetime(2026, 4, 29)}) == date(2026, 4, 29)


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
    provider = IsYatirimProvider(session=SESSION, _transport=httpx.MockTransport(handler=lambda r: httpx.Response(200)))
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
    provider = IsYatirimProvider(session=SESSION, _transport=httpx.MockTransport(handler=lambda r: httpx.Response(200)))
    assert provider._daily_rows_to_bars("THYAO", rows) == []


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
