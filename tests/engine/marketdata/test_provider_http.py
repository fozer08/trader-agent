import asyncio
import logging
from datetime import time
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_agent.engine.marketdata.base import ProviderResponseError, ProviderTimeoutError
from trader_agent.engine.calendar import ExchangeSchedule, TradingCalendar
from trader_agent.engine.marketdata.provider import IsYatirimProvider

CALENDAR = TradingCalendar(
    ExchangeSchedule(time(10), time(18), ZoneInfo("Europe/Istanbul")), {}, frozenset({2026}),
)
DAILY_PAYLOAD = {
    "ok": True,
    "value": [{"HGDG_TARIH": "06-10-2026", "HGDG_KAPANIS": 100}],
}


@pytest.mark.parametrize(
    "error_type",
    [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError],
)
async def test_daily_request_recovers_from_temporary_network_error(monkeypatch, error_type):
    requests = []
    sleep = AsyncMock()
    monkeypatch.setattr("trader_agent.engine.marketdata.provider.asyncio.sleep", sleep)

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise error_type("", request=request)
        return httpx.Response(200, json=DAILY_PAYLOAD)

    async with IsYatirimProvider(
        CALENDAR, _transport=httpx.MockTransport(handler),
    ) as provider:
        acquire = AsyncMock()
        monkeypatch.setattr(provider._rate_limiter, "acquire", acquire)
        bars = await provider.get_daily("THYAO", 1)

    assert len(bars.bars) == 1 and bars.bars[0].close == 100
    assert len(requests) == 2
    assert acquire.await_count == 2
    sleep.assert_awaited_once_with(1)
    assert requests[0].url == requests[1].url
    assert all(r.headers["X-Requested-With"] == "XMLHttpRequest" for r in requests)


@pytest.mark.parametrize("max_retries", [0, 1, 3])
async def test_timeout_retries_are_bounded_and_logged(monkeypatch, caplog, max_retries):
    requests = []
    sleep = AsyncMock()
    monkeypatch.setattr("trader_agent.engine.marketdata.provider.asyncio.sleep", sleep)

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("", request=request)

    async with IsYatirimProvider(
        CALENDAR, _transport=httpx.MockTransport(handler), max_retries=max_retries,
    ) as provider:
        with caplog.at_level(logging.WARNING), pytest.raises(ProviderTimeoutError):
            await provider.get_daily("THYAO", 1)

    assert len(requests) == max_retries + 1
    assert sleep.await_count == max_retries
    assert "THYAO" in caplog.text
    assert "HisseTekil" in caplog.text
    assert "ReadTimeout" in caplog.text
    assert caplog.records[-1].levelno == logging.ERROR


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_http_status_errors_are_not_retried(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    async with IsYatirimProvider(
        CALENDAR, _transport=httpx.MockTransport(handler),
    ) as provider:
        with pytest.raises(ProviderResponseError) as error:
            await provider.get_daily("THYAO", 1)

    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    assert error.value.__cause__.response.status_code == status
    assert len(requests) == 1


@pytest.mark.parametrize("timeout", [None, 5, 45])
async def test_requests_use_configured_timeout(timeout):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=DAILY_PAYLOAD)

    options = {} if timeout is None else {"timeout": timeout}
    async with IsYatirimProvider(
        CALENDAR, _transport=httpx.MockTransport(handler), **options,
    ) as provider:
        await provider.get_daily("THYAO", 1)

    expected = 30 if timeout is None else timeout
    assert requests[0].extensions["timeout"]["read"] == expected
    assert requests[0].extensions["timeout"]["connect"] == min(expected, 10)


async def test_cancellation_during_retry_stops_requests():
    requests = []
    attempted = asyncio.Event()

    def handler(request):
        requests.append(request)
        attempted.set()
        raise httpx.ReadTimeout("", request=request)

    async with IsYatirimProvider(
        CALENDAR, _transport=httpx.MockTransport(handler),
    ) as provider:
        task = asyncio.create_task(provider.get_daily("THYAO", 1))
        await attempted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(requests) == 1
