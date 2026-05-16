from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Annotated, ClassVar

from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator

from ..market.base import TradingSession
from .base import BaseConfig, data_dir


def _parse_time(v: object) -> time:
    if isinstance(v, time):
        return v
    if isinstance(v, str):
        parts = v.split(":")
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
    raise ValueError(f"Cannot parse time: {v!r}")


TimeField = Annotated[time, BeforeValidator(_parse_time)]


class SessionConfig(BaseModel):
    model_config = {"extra": "forbid"}

    open: TimeField
    close: TimeField


class MarketTypeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    session: SessionConfig


class ExchangeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    mic: str
    timezone: str
    symbol_suffix: str
    markets: dict[str, MarketTypeConfig]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def trading_session(self, market: str) -> TradingSession:
        session = self.markets[market].session
        return TradingSession(start=session.open, end=session.close, timezone=self.tz)


class MarketConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "market.yaml"

    watchlist: str
    exchanges: dict[str, ExchangeConfig]

    def watchlist_path(self) -> Path:
        return data_dir() / self.watchlist
