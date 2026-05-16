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


class ExchangeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    code: str
    mic: str
    timezone: str
    open: TimeField
    close: TimeField

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def trading_session(self) -> TradingSession:
        return TradingSession(start=self.open, end=self.close, timezone=self.tz)


class MarketConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "market.yaml"

    watchlist: str
    exchange: ExchangeConfig

    def watchlist_path(self) -> Path:
        return data_dir() / self.watchlist
