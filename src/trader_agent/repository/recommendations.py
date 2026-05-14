from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal

from sqlalchemy import DateTime, Float, Integer, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ._helpers import normalize_symbol, utc_now, validate_price
from .base import Base

Decision = Literal["BUY", "SELL"]

_RETENTION = timedelta(days=10)


@dataclass(frozen=True)
class Recommendation:
    """Agent'ın verdiği bir AL/SAT önerisinin kaydı."""

    id: int
    symbol: str
    decision: Decision
    entry: float
    stop: float
    target: float | None
    rationale: str | None
    created_at: datetime


class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RecommendationRepository:
    """AL/SAT önerilerinin kaydı; 10 günden eski kayıtlar otomatik silinir."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        symbol: str,
        decision: Decision,
        entry: float,
        stop: float,
        target: float | None = None,
        rationale: str | None = None,
    ) -> Recommendation:
        symbol = normalize_symbol(symbol)
        if decision not in ("BUY", "SELL"):
            raise ValueError("decision must be 'BUY' or 'SELL'.")
        validate_price(entry, "entry")
        validate_price(stop, "stop")
        if target is not None:
            validate_price(target, "target")

        now = utc_now()
        with self._session_factory() as session:
            session.execute(delete(RecommendationORM).where(RecommendationORM.created_at < now - _RETENTION))
            row = RecommendationORM(
                symbol=symbol,
                decision=decision,
                entry=entry,
                stop=stop,
                target=target,
                rationale=rationale,
                created_at=now,
            )
            session.add(row)
            session.commit()
            return _to_recommendation(row)

    def list(self, symbol: str | None = None) -> list[Recommendation]:
        """Son 10 günün önerilerini en yeniden eskiye döner; symbol verilirse filtrelenir."""
        cutoff = utc_now() - _RETENTION
        with self._session_factory() as session:
            stmt = select(RecommendationORM).where(RecommendationORM.created_at >= cutoff)
            if symbol is not None:
                stmt = stmt.where(RecommendationORM.symbol == normalize_symbol(symbol))
            stmt = stmt.order_by(RecommendationORM.created_at.desc())
            rows = session.execute(stmt).scalars().all()
            return [_to_recommendation(r) for r in rows]


# ---- helpers -----------------------------------------------------------------

def _to_recommendation(row: RecommendationORM) -> Recommendation:
    return Recommendation(
        id=row.id,
        symbol=row.symbol,
        decision=row.decision,  # type: ignore[arg-type]
        entry=row.entry,
        stop=row.stop,
        target=row.target,
        rationale=row.rationale,
        created_at=row.created_at,
    )
