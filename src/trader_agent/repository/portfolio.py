from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import DateTime, Float, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base


@dataclass(frozen=True)
class Position:
    """Portföyde tek bir pozisyon. avg_cost, stop_loss, target hisse başına TL."""

    symbol: str
    quantity: int
    avg_cost: float
    updated_at: datetime
    stop_loss: float | None = None
    target: float | None = None


class PositionORM(Base):
    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionRepository:
    """Portföy CRUD'u; her metod kendi transaction'ında çalışır."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def add(
        self,
        symbol: str,
        quantity: int,
        avg_cost: float,
        stop_loss: float | None = None,
        target: float | None = None,
    ) -> tuple[Position, Position | None]:
        """Pozisyon ekler veya weighted-average ile günceller.

        Quantity ve avg_cost weighted-average ile birleşir.
        stop_loss/target verilirse mevcut değerin üzerine yazılır; None ise korunur.
        Döner: (yeni_pozisyon, önceki_pozisyon_veya_None).
        """
        symbol = _normalize_symbol(symbol)
        _validate_quantity(quantity)
        _validate_price(avg_cost, "avg_cost")
        if stop_loss is not None:
            _validate_price(stop_loss, "stop_loss")
        if target is not None:
            _validate_price(target, "target")

        with self._session_factory() as session:
            row = session.get(PositionORM, symbol)
            previous = _to_position(row) if row is not None else None

            if row is None:
                row = PositionORM(
                    symbol=symbol,
                    quantity=quantity,
                    avg_cost=avg_cost,
                    stop_loss=stop_loss,
                    target=target,
                    updated_at=_utc_now(),
                )
                session.add(row)
            else:
                new_qty = row.quantity + quantity
                row.avg_cost = (row.quantity * row.avg_cost + quantity * avg_cost) / new_qty
                row.quantity = new_qty
                if stop_loss is not None:
                    row.stop_loss = stop_loss
                if target is not None:
                    row.target = target
                row.updated_at = _utc_now()

            session.commit()
            return _to_position(row), previous

    def set_levels(
        self,
        symbol: str,
        stop_loss: float | None = None,
        target: float | None = None,
    ) -> Position:
        """Sadece stop_loss ve/veya target günceller; quantity'ye dokunmaz.

        None geçilen alan korunur. Pozisyon yoksa KeyError fırlatır.
        """
        if stop_loss is None and target is None:
            raise ValueError("stop_loss veya target'tan en az biri verilmeli.")
        if stop_loss is not None:
            _validate_price(stop_loss, "stop_loss")
        if target is not None:
            _validate_price(target, "target")

        symbol = _normalize_symbol(symbol)
        with self._session_factory() as session:
            row = session.get(PositionORM, symbol)
            if row is None:
                raise KeyError(symbol)
            if stop_loss is not None:
                row.stop_loss = stop_loss
            if target is not None:
                row.target = target
            row.updated_at = _utc_now()
            session.commit()
            return _to_position(row)

    def remove(self, symbol: str) -> bool:
        symbol = _normalize_symbol(symbol)
        with self._session_factory() as session:
            row = session.get(PositionORM, symbol)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def clear(self) -> int:
        with self._session_factory() as session:
            count = session.query(PositionORM).delete()
            session.commit()
            return count

    def list(self) -> list[Position]:
        with self._session_factory() as session:
            rows = session.execute(select(PositionORM).order_by(PositionORM.symbol)).scalars().all()
            return [_to_position(row) for row in rows]


# ---- helpers -----------------------------------------------------------------

def _to_position(row: PositionORM) -> Position:
    return Position(
        symbol=row.symbol,
        quantity=row.quantity,
        avg_cost=row.avg_cost,
        stop_loss=row.stop_loss,
        target=row.target,
        updated_at=row.updated_at,
    )


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol cannot be empty.")
    return normalized


def _validate_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValueError("quantity must be an integer.")
    if quantity <= 0:
        raise ValueError("quantity must be positive.")


def _validate_price(value: float, field: str) -> None:
    if value <= 0:
        raise ValueError(f"{field} must be positive.")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
