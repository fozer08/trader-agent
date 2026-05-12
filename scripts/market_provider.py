from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trader_agent.config.market import MarketConfig
from trader_agent.market.provider import IsYatirimProvider
from trader_agent.market.types import Bar, IntradaySnapshot, TimeFrame


SYMBOL = "THYAO"


async def main() -> int:
    config = MarketConfig.load()
    session = config.exchanges["bist"].trading_session("equities")

    async with IsYatirimProvider(session=session, timeout=8) as provider:
        print(f"IsYatirimProvider live API samples for {SYMBOL}")
        print()

        daily = await provider.get_daily(SYMBOL, period=10)
        _print_bars("get_daily(period=10)", daily)

        intraday_m1 = await provider.get_intraday(SYMBOL, tf=TimeFrame.M1)
        _print_bars("get_intraday(tf=M1)", intraday_m1)

        intraday_m5 = await provider.get_intraday(SYMBOL, tf=TimeFrame.M5)
        _print_bars("get_intraday(tf=M5)", intraday_m5)

        intraday_m15 = await provider.get_intraday(SYMBOL, tf=TimeFrame.M15)
        _print_bars("get_intraday(tf=M15)", intraday_m15)

        today = await provider.get_today(SYMBOL)
        _print_snapshots("get_today()", [today] if today is not None else [])

    return 0


def _print_bars(
    title: str,
    bars: list[Bar],
    sample_size: int = 5,
) -> None:
    print(title)
    print("-" * len(title))
    print(f"count: {len(bars)}")

    if not bars:
        print("no bars returned")
        print()
        return

    samples = bars if len(bars) <= sample_size else bars[-sample_size:]
    print(f"showing latest {len(samples)}:")
    print(
        f"{'datetime':<25} {'tf':<4} {'open':>10} {'high':>10} "
        f"{'low':>10} {'close':>10} {'volume':>12}"
    )
    for bar in samples:
        print(
            f"{bar.datetime.isoformat():<25} "
            f"{bar.timeframe.name:<4} "
            f"{_format_ohlcv(bar)}"
        )
    print()


def _print_snapshots(
    title: str,
    snapshots: list[IntradaySnapshot],
) -> None:
    print(title)
    print("-" * len(title))
    print(f"count: {len(snapshots)}")

    if not snapshots:
        print("no snapshot returned")
        print()
        return

    print(
        f"{'datetime':<25} {'open':>10} {'high':>10} "
        f"{'low':>10} {'close':>10} {'volume':>12}"
    )
    for snapshot in snapshots:
        print(f"{snapshot.datetime.isoformat():<25} {_format_ohlcv(snapshot)}")
    print()


def _format_ohlcv(row: Bar | IntradaySnapshot) -> str:
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
