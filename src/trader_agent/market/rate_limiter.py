from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket rate limiter.

    Dakikada calls_per_minute token üretir; her acquire() bir token harcar,
    token yoksa dolana kadar bekler.
    """

    def __init__(self, calls_per_minute: int) -> None:
        self._rate = calls_per_minute / 60.0   # token/saniye
        self._capacity = float(calls_per_minute)
        self._tokens = float(calls_per_minute)  # başlangıçta dolu
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # _last'tan bu yana geçen süreye göre token ekle; capacity'yi aşma
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._last) * self._rate,
            )
            self._last = now

            if self._tokens < 1.0:
                # 1 token birikimine tam yetecek kadar bekle
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
                # _last'ı sleep sonrasına çek: yoksa bir sonraki acquire() sleep
                # süresini tekrar token olarak ekler, rate limiting devre dışı kalır
                self._last = time.monotonic()
            else:
                self._tokens -= 1.0
