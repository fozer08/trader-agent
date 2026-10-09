from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from trader_agent.engine.marketdata import rate_limiter
from trader_agent.engine.marketdata.rate_limiter import RateLimiter


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    async def sleep(self, seconds):
        self.waits.append(seconds)
        await asyncio.sleep(0)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(rate_limiter, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(rate_limiter, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=clock.sleep))
    return clock


async def test_initial_capacity_is_available_without_waiting(clock):
    limiter = RateLimiter(calls_per_minute=2)
    await limiter.acquire()
    await limiter.acquire()
    assert clock.waits == []
    await limiter.acquire()
    assert clock.waits == [30]


async def test_elapsed_time_refills_tokens(clock):
    limiter = RateLimiter(calls_per_minute=2)
    await limiter.acquire()
    await limiter.acquire()
    clock.now += 15
    await limiter.acquire()
    assert clock.waits == [15]
    await limiter.acquire()
    assert clock.waits == [15, 30]


async def test_long_idle_period_does_not_exceed_capacity(clock):
    limiter = RateLimiter(calls_per_minute=2)
    await limiter.acquire()
    await limiter.acquire()
    clock.now += 3600
    await limiter.acquire()
    await limiter.acquire()
    assert clock.waits == []
    await limiter.acquire()
    assert clock.waits == [30]


async def test_concurrent_requests_share_the_same_rate_limit(clock):
    limiter = RateLimiter(calls_per_minute=2)
    await limiter.acquire()
    await limiter.acquire()
    await asyncio.gather(limiter.acquire(), limiter.acquire(), limiter.acquire())
    assert clock.waits == [30, 30, 30]
    assert clock.now == 90


async def test_cancelled_wait_releases_lock(clock, monkeypatch):
    limiter = RateLimiter(calls_per_minute=1)
    await limiter.acquire()
    waiting = asyncio.Event()

    async def wait_until_cancelled(seconds):
        waiting.set()
        await asyncio.Future()

    monkeypatch.setattr(rate_limiter.asyncio, "sleep", wait_until_cancelled)
    task = asyncio.create_task(limiter.acquire())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(rate_limiter.asyncio, "sleep", clock.sleep)
    await limiter.acquire()
    assert clock.waits == [60]
