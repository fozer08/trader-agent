from __future__ import annotations

import asyncio
import time

import pytest

from trader_agent.market.rate_limiter import RateLimiter


# ---- Initial state -----------------------------------------------------------

def test_starts_full():
    """Bucket başlangıçta dolu; capacity = calls_per_minute."""
    rl = RateLimiter(calls_per_minute=10)
    assert rl._tokens == pytest.approx(10.0)
    assert rl._capacity == 10.0


def test_rate_is_per_second():
    """_rate = calls_per_minute / 60."""
    rl = RateLimiter(calls_per_minute=600)
    assert rl._rate == pytest.approx(10.0)  # 10 token/sec


# ---- Acquire while full ------------------------------------------------------

async def test_acquire_while_full_does_not_sleep():
    """Dolu bucket'ta peş peşe acquire'lar anında geçer."""
    rl = RateLimiter(calls_per_minute=6000)  # capacity=6000
    start = time.monotonic()
    for _ in range(100):
        await rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"100 acquire dolu bucket'tan {elapsed:.3f}s sürdü (beklenen <0.1)"


async def test_acquire_decrements_token_count():
    """Her acquire bir token harcar."""
    rl = RateLimiter(calls_per_minute=60)  # capacity=60
    await rl.acquire()
    await rl.acquire()
    await rl.acquire()
    # 60 - 3 = 57; refill bir miktar token ekleyebilir ama maks ~1-2 olur
    assert rl._tokens < 58


# ---- Acquire when drained ----------------------------------------------------

async def test_acquire_when_empty_blocks_until_refill():
    """Token bittiğinde 1 token üretimine kadar bekler."""
    rl = RateLimiter(calls_per_minute=600)  # 10 token/sec → 1 token = 100ms
    rl._tokens = 0.0
    rl._last = time.monotonic()

    start = time.monotonic()
    await rl.acquire()
    elapsed = time.monotonic() - start
    assert 0.08 < elapsed < 0.25, (
        f"Boş bucket'tan acquire {elapsed:.3f}s sürdü (beklenen ~0.1s)"
    )


async def test_refill_after_time_makes_acquire_instant():
    """Zaman geçtikten sonra token'lar geri dolar, acquire bekleme yapmaz."""
    rl = RateLimiter(calls_per_minute=600)  # 10 token/sec
    rl._tokens = 0.0
    rl._last = time.monotonic() - 1.0  # 1 saniye önce → 10 token birikti

    start = time.monotonic()
    await rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"Refill sonrası acquire {elapsed:.3f}s sürdü (beklenen <0.05)"


# ---- Capacity cap ------------------------------------------------------------

async def test_refill_never_exceeds_capacity():
    """Uzun beklemede de token sayısı capacity'yi geçmez."""
    rl = RateLimiter(calls_per_minute=60)  # capacity=60
    rl._tokens = 0.0
    rl._last = time.monotonic() - 3600  # 1 saat önce → kapsadan çok fazla token "üretirdi"

    await rl.acquire()
    # acquire sonrası _tokens en fazla capacity-1 olabilir
    assert rl._tokens <= 60.0


# ---- Concurrent acquires -----------------------------------------------------

async def test_concurrent_acquires_serialize_via_lock():
    """Aynı anda 5 acquire çağrılırsa lock ile sıraya girip hepsi tamamlanır."""
    rl = RateLimiter(calls_per_minute=6000)  # capacity=6000, bol token
    results = await asyncio.gather(*[rl.acquire() for _ in range(5)])
    assert results == [None] * 5  # acquire None döner


async def test_concurrent_acquires_when_drained_total_wait_proportional():
    """3 acquire boş bucket'tan: toplam bekleme yaklaşık 3 × token_süresi."""
    rl = RateLimiter(calls_per_minute=600)  # 10/sec → 100ms per token
    rl._tokens = 0.0
    rl._last = time.monotonic()

    start = time.monotonic()
    await asyncio.gather(rl.acquire(), rl.acquire(), rl.acquire())
    elapsed = time.monotonic() - start
    # Üç token üretimi gerek: ~300ms; lock seri ettiği için paralel hızlanma yok
    assert 0.25 < elapsed < 0.6, (
        f"3 acquire boş bucket'tan {elapsed:.3f}s sürdü (beklenen ~0.3s)"
    )
