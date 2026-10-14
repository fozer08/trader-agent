from __future__ import annotations

import asyncio

from ..analysis import policy_for
from ..marketdata.service import MarketService
from ...utils.logging import get_logger

_log = get_logger(__name__)

# Warm the longest supported horizon; analysis remains the source of window sizes.
_DEFAULT_PERIOD = policy_for("position").history_bars


async def run(
    market: MarketService,
    watchlist: list[dict],
    period: int = _DEFAULT_PERIOD,
) -> dict[str, bool]:
    """Watchlist için D1 barları cache'e ısıt.

    MarketService üzerinden çağırıyoruz çünkü cache (MarketBarCache) servisin
    içinde — provider'a doğrudan gidersek cache'i ısıtmaz, prefetch işe yaramaz.
    Provider'ın rate_limiter'ı paralelliği zaten throttle ediyor.
    """
    if not watchlist:
        return {}

    symbols = [entry["symbol"] for entry in watchlist]

    async def _one(symbol: str) -> bool:
        try:
            await market.get_daily(symbol, period=period)
            return True
        except Exception as exc:
            _log.warning("prefetch failed for %s: %s", symbol, exc)
            return False

    results = await asyncio.gather(*(_one(s) for s in symbols))
    summary = dict(zip(symbols, results))
    success = sum(1 for ok in results if ok)
    _log.info("prefetch_market: %d/%d symbols warmed", success, len(symbols))
    return summary
