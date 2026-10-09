"""Takvim dosyasının dış şeması ve çalışma zamanı takvimine dönüşümü."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from ...config.market import MarketConfig, WEEKDAYS
from ...utils.time import TimeField
from .core import DaySchedule, ExchangeSchedule, TradingCalendar, TradingSession


class _CalendarEntry(BaseModel):
    model_config = {"extra": "forbid"}

    date: dt.date
    name: str = Field(min_length=1)

    @field_validator("date", mode="before")
    @classmethod
    def iso_date(cls, value: object) -> dt.date:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("Tatil tarihi YYYY-MM-DD biçiminde olmalıdır.")
        result = dt.date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError("Tatil tarihi YYYY-MM-DD biçiminde olmalıdır.")
        return result


class _ClosedDay(_CalendarEntry):
    type: Literal["closed"]


class _HalfDay(_CalendarEntry):
    type: Literal["half_day"]
    close: TimeField
    open: TimeField | None = None
    daily_close: TimeField | None = None


class _CalendarDocument(BaseModel):
    model_config = {"extra": "forbid"}

    exchange: str = Field(min_length=1)
    covered_years: list[StrictInt] = Field(min_length=1)
    days: list[Annotated[_ClosedDay | _HalfDay, Field(discriminator="type")]]

    @model_validator(mode="after")
    def unique_dates_and_years(self) -> _CalendarDocument:
        years = set(self.covered_years)
        if len(years) != len(self.covered_years) or any(not 1 <= year <= 9999 for year in years):
            raise ValueError("Takvim yılları tekrarlı veya geçersiz olamaz.")
        dates = [day.date for day in self.days]
        if len(set(dates)) != len(dates):
            raise ValueError("Takvimde tekrarlanan tarih var.")
        return self


def load_calendar(config: MarketConfig) -> TradingCalendar:
    """JSON'u bir kez okur; yalnız doğrulanmış, tarihli seansları takvime verir."""
    document = _CalendarDocument.model_validate_json(
        config.calendar_path().read_text(encoding="utf-8")
    )
    exchange = config.exchange
    if document.exchange != exchange.mic:
        raise ValueError(f"Takvim exchange değeri {exchange.mic} olmalıdır: {document.exchange}")
    schedule = ExchangeSchedule(
        start=exchange.open,
        end=exchange.close,
        daily_end=exchange.daily_close,
        timezone=ZoneInfo(exchange.timezone),
        weekdays=tuple(WEEKDAYS.index(day) for day in exchange.weekdays),
    )
    exceptions = {}
    for entry in document.days:
        if isinstance(entry, _ClosedDay):
            day = DaySchedule(entry.date, "closed", None, entry.name)
        else:
            session = TradingSession(
                dt.datetime.combine(entry.date, entry.open or schedule.start, schedule.timezone),
                dt.datetime.combine(entry.date, entry.close, schedule.timezone),
                dt.datetime.combine(entry.date, entry.daily_close or entry.close, schedule.timezone),
            )
            day = DaySchedule(entry.date, "half_day", session, entry.name)
        exceptions[entry.date] = day
    return TradingCalendar(schedule, exceptions, frozenset(document.covered_years))
