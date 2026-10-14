"""Portföyün I/O'dan bağımsız kayıtları ve ağırlıklı maliyet hesabı."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from .analysis import Horizon

Source = Literal["manual", "system"]
Kind = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Position:
    """Tek planın miktar ve maliyet görünümü; aynı hisse birden fazla plan taşıyabilir."""

    id: str
    symbol: str
    quantity: int
    avg_cost: float
    opened_at: datetime | None
    stop_loss: float | None
    target: float | None
    note: str
    realized_pnl: float
    horizon: Horizon
    source: Source
    recommendation_id: str | None
    producer: str | None
    initial_plan: dict
    plan_changed: bool
    created_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class PositionTransaction:
    """Pozisyon kimliğine bağlı, kaynaklar arasında karışmayan miktar hareketi."""

    id: int
    position_id: str
    symbol: str
    kind: Kind
    quantity: int
    price: float
    created_at: datetime
    realized_pnl: float
    released_cost: float


@dataclass(frozen=True)
class ClosedPosition:
    """Kapanan planın toplam maliyet ve sonucunu koruyan görünüm."""

    id: str
    symbol: str
    opened_at: datetime | None
    closed_at: datetime
    quantity: int
    total_invested: float
    total_received: float
    source: Source
    producer: str | None
    horizon: Horizon
    recommendation_id: str | None

    @property
    def realized_pnl(self) -> float:
        return self.total_received - self.total_invested

    @property
    def realized_pnl_pct(self) -> float:
        return self.realized_pnl / self.total_invested * 100

    @property
    def avg_buy_cost(self) -> float:
        return self.total_invested / self.quantity

    @property
    def avg_sell_price(self) -> float:
        return self.total_received / self.quantity

    @property
    def hold_days(self) -> int | None:
        return holding_days(self.opened_at, self.closed_at)


def holding_days(opened_at: datetime | None, at: datetime) -> int | None:
    return max(1, (at.date() - opened_at.date()).days + 1) if opened_at else None


class Trade(Protocol):
    """Maliyet hesabının veritabanından bağımsız ihtiyaç duyduğu hareket alanları."""

    @property
    def kind(self) -> str: ...
    @property
    def quantity(self) -> int: ...
    @property
    def price(self) -> float: ...


@dataclass(frozen=True)
class PositionState:
    """Hareket sırasıyla hesaplanan miktar, ortalama maliyet ve kapanan kâr/zarar."""

    net_qty: int
    total_bought: int
    avg_cost: float
    total_invested: float
    total_received: float
    realized_pnl: float


def compute_state(txs: Sequence[Trade]) -> PositionState:
    quantity = bought = 0
    cost = invested = received = realized = Decimal(0)
    for tx in txs:
        price = Decimal(str(tx.price))
        if tx.kind == "BUY":
            cost = (quantity * cost + tx.quantity * price) / (quantity + tx.quantity)
            quantity += tx.quantity
            bought += tx.quantity
            invested += tx.quantity * price
        else:
            if tx.quantity > quantity:
                raise ValueError("Pozisyon miktarından fazla kapatılamaz.")
            quantity -= tx.quantity
            received += tx.quantity * price
            realized += tx.quantity * (price - cost)
    return PositionState(
        quantity, bought, float(cost), float(invested), float(received), float(realized)
    )


def preview_position(
    quantity: int,
    price: float,
    stop_loss: float | None = None,
    target: float | None = None,
    avg_cost: float | None = None,
) -> dict[str, float | None]:
    """Kayıt paneli tutarlarını hesaplar; UI bu hesabı tekrar uygulamaz."""
    q, p = Decimal(quantity), Decimal(str(price))
    return {
        "amount": float(q * p),
        "planned_loss": float(q * (p - Decimal(str(stop_loss)))) if stop_loss is not None else None,
        "planned_gain": float(q * (Decimal(str(target)) - p)) if target is not None else None,
        "realized_pnl": float(q * (p - Decimal(str(avg_cost)))) if avg_cost is not None else None,
    }


def summarize_performance(
    closed: Sequence[ClosedPosition], realized: Sequence[tuple[float, float]]
) -> dict[str, float | int | None]:
    """Seçilen kapanışların sonucunu toplar; öneriler veya açık değerleme bu hesaba girmez."""
    pnl = float(sum((Decimal(str(value)) for value, _ in realized), Decimal(0)))
    cost = float(sum((Decimal(str(value)) for _, value in realized), Decimal(0)))
    wins = sum(round(c.realized_pnl, 2) > 0 for c in closed)
    losses = sum(round(c.realized_pnl, 2) < 0 for c in closed)
    days = [c.hold_days for c in closed if c.hold_days is not None]
    return {
        "realized_pnl": round(pnl, 2),
        "released_cost": round(cost, 2),
        "realized_return_pct": pnl / cost * 100 if cost else None,
        "closed_count": len(closed),
        "wins": wins,
        "losses": losses,
        "breakeven": len(closed) - wins - losses,
        "win_rate": wins / len(closed) * 100 if closed else None,
        "average_hold_days": sum(days) / len(days) if days else None,
        "known_hold_count": len(days),
    }
