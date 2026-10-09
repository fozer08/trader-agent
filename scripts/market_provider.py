from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from trader_agent.engine.marketdata.types import OHLCV, BarData, SessionSnapshot


SYMBOL = "THYAO"


async def main() -> int:
    src = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(src))

    from trader_agent.config.market import MarketConfig
    from trader_agent.env import load_env
    from trader_agent.engine.marketdata.cache import MarketBarCache
    from trader_agent.engine.calendar.loader import load_calendar
    from trader_agent.engine.marketdata.provider import IsYatirimProvider
    from trader_agent.engine.marketdata.service import MarketService
    from trader_agent.engine.marketdata.types import TimeFrame

    load_env()
    config = MarketConfig.load()
    calendar = load_calendar(config)

    bar_store = MarketBarCache()
    async with IsYatirimProvider(calendar=calendar, timeout=8, store=bar_store) as provider:
        market = MarketService(provider, store=bar_store)
        print(f"MarketService live API samples for {SYMBOL}")
        print()

        daily = await market.get_daily(SYMBOL, period=10)
        _print_bars("get_daily(period=10)", daily)

        intraday_m1 = await market.get_intraday(SYMBOL, tf=TimeFrame.M1)
        _print_bars("get_intraday(tf=M1)", intraday_m1)

        intraday_m5 = await market.get_intraday(SYMBOL, tf=TimeFrame.M5)
        _print_bars("get_intraday(tf=M5)", intraday_m5)

        intraday_m15 = await market.get_intraday(SYMBOL, tf=TimeFrame.M15)
        _print_bars("get_intraday(tf=M15)", intraday_m15)

        today = await market.get_today(SYMBOL)
        _print_snapshots("get_today()", [today] if today is not None else [])

    return 0


def _print_bars(
    title: str,
    series: BarData,
    sample_size: int = 5,
) -> None:
    print(title)
    print("-" * len(title))
    bars = series.bars
    print(f"symbol: {series.symbol}, timeframe: {series.timeframe.name}")
    print(f"count: {len(bars)}")

    if not bars:
        print("no bars returned")
        print()
        return

    samples = bars if len(bars) <= sample_size else bars[-sample_size:]
    print(f"showing latest {len(samples)}:")
    print(
        f"{'period_start':<25} {'open':>10} {'high':>10} "
        f"{'low':>10} {'close':>10} {'volume':>12}"
    )
    for bar in samples:
        print(
            f"{bar.period_start.isoformat():<25} "
            f"{_format_ohlcv(bar)}"
        )
    print()


def _print_snapshots(
    title: str,
    snapshots: list[SessionSnapshot],
) -> None:
    print(title)
    print("-" * len(title))
    print(f"count: {len(snapshots)}")

    if not snapshots:
        print("no snapshot returned")
        print()
        return

    print(
        f"{'as_of':<25} {'open':>10} {'high':>10} "
        f"{'low':>10} {'close':>10} {'volume':>12}"
    )
    for snapshot in snapshots:
        print(f"{snapshot.as_of.isoformat():<25} {_format_ohlcv(snapshot.ohlcv)}")
    print()


def _format_ohlcv(row: OHLCV) -> str:
    volume = "-" if row.volume is None else f"{row.volume:.0f}"
    return (
        f"{row.open:>10.2f} "
        f"{row.high:>10.2f} "
        f"{row.low:>10.2f} "
        f"{row.close:>10.2f} "
        f"{volume:>12}"
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
