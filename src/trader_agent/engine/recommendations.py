from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Callable

from .analysis import Horizon, RiskLevel
from .calendar import TradingCalendar
from .repository.recommendations import Producer, Recommendation, RecommendationRepository
from .repository.helpers import utc_now


class RecommendationService:
    """Sohbet ve bağımsız üreticiler için tek yayınlama ve günlük geçerlilik kuralı."""

    def __init__(
        self,
        repository: RecommendationRepository,
        calendar: TradingCalendar,
        require_symbol: Callable[[str], str],
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.calendar = calendar
        self.require_symbol = require_symbol
        self.now = now

    async def publish(
        self,
        *,
        symbol: str,
        entry_min: float,
        entry_max: float,
        stop_loss: float,
        target: float,
        horizon: Horizon,
        rationale: str,
        data_as_of: datetime,
        producer: Producer,
        producer_version: str,
        risk_level: RiskLevel | None = None,
    ) -> Recommendation:
        symbol = self.require_symbol(symbol)
        now = self.now().astimezone(self.calendar.timezone)
        expires = datetime.combine(now.date() + timedelta(days=1), time(), self.calendar.timezone)
        if horizon == "intraday":
            day = self.calendar.day(now.date())
            if not day.session or now >= day.session.end:
                raise ValueError(
                    "Seans içi öneriler yalnız bugünkü seans bitmeden oluşturulabilir."
                )
            expires = day.session.end
        return await asyncio.to_thread(
            self.repository.publish,
            symbol=symbol,
            entry_min=entry_min,
            entry_max=entry_max,
            stop_loss=stop_loss,
            target=target,
            horizon=horizon,
            rationale=rationale,
            risk_level=risk_level,
            producer=producer,
            producer_version=producer_version,
            at=now,
            expires_at=expires,
            data_as_of=data_as_of,
        )
