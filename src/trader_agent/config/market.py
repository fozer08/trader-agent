from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator

from ..utils.time import TimeField
from .base import BaseConfig, data_dir

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ExchangeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = "Borsa İstanbul"
    mic: str = "XIST"
    timezone: str = "Europe/Istanbul"
    open: TimeField = time(10, 0)
    close: TimeField = time(18, 0)
    daily_close: TimeField | None = None
    weekdays: list[Weekday] = Field(default_factory=lambda: list(WEEKDAYS[:5]), min_length=1)

    @model_validator(mode="after")
    def validate_schedule(self) -> ExchangeConfig:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Geçersiz timezone: {self.timezone}") from exc
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("Çalışma günleri tekrarlı olamaz.")
        if not self.open < self.close <= (self.daily_close or self.close):
            raise ValueError("open < close <= daily_close olmalıdır.")
        return self


class MarketConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "market.yaml"

    stocklist: str = "stocklist.json"
    calendar: str = Field(default="calendar.json", min_length=1)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)

    def stocklist_path(self) -> Path:
        return data_dir() / self.stocklist

    def calendar_path(self) -> Path:
        return data_dir() / self.calendar
