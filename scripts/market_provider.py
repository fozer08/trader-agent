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
from trader_agent.market.types import Bar, TimeFrame


SYMBOL = "THYAO"


async def main() -> int:
    config = MarketConfig.load()
    session = config.exchanges["bist"].trading_session("equities")

    async with IsYatirimProvider(session=session, timeout=8) as provider:
        print(f"IsYatirimProvider live API samples for {SYMBOL}")
        print()

        daily = await provider.get_daily(SYMBOL, period=10)
        _print_bars("get_daily(period=10)", daily)

        intraday_m5 = await provider.get_intraday(SYMBOL, tf=TimeFrame.M5)
        _print_bars("get_intraday(tf=M5)", intraday_m5)

        intraday_m15 = await provider.get_intraday(SYMBOL, tf=TimeFrame.M15)
        _print_bars("get_intraday(tf=M15)", intraday_m15)

        today = await provider.get_today(SYMBOL)
        _print_bars("get_today()", [today] if today is not None else [])

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
        f"{'low':>10} {'close':>10} {'volume':>12} {'closed':>7}"
    )
    for bar in samples:
        volume = "-" if bar.volume is None else f"{bar.volume:.0f}"
        print(
            f"{bar.datetime.isoformat():<25} "
            f"{bar.timeframe.name:<4} "
            f"{bar.open:>10.2f} "
            f"{bar.high:>10.2f} "
            f"{bar.low:>10.2f} "
            f"{bar.close:>10.2f} "
            f"{volume:>12} "
            f"{str(bar.is_closed):>7}"
        )
    print()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
