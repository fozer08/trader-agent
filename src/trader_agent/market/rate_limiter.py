from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket rate limiter.

    Başlangıçta ``calls_per_minute`` kadar token bulunur. Her ``acquire()``
    çağrısı 1 token harcar; token yoksa dolana kadar bekler.
    Token'lar dakikada ``calls_per_minute`` hızında yenilenir.
    """

    def __init__(self, calls_per_minute: int) -> None:
        self._rate = calls_per_minute / 60.0   # token/saniye
        self._capacity = float(calls_per_minute)
        self._tokens = float(calls_per_minute)  # dolu başla
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0
