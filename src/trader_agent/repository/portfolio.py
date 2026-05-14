from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    delete,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from ._helpers import normalize_symbol, utc_now, validate_price
from .base import Base

Kind = Literal["BUY", "SELL"]


# ---- ORM ---------------------------------------------------------------------

class PositionORM(Base):
    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("stop_loss IS NULL OR stop_loss > 0", name="ck_position_stop_positive"),
        CheckConstraint("target IS NULL OR target > 0", name="ck_position_target_positive"),
    )


class PositionTransactionORM(Base):
    __tablename__ = "position_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String, ForeignKey("positions.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('BUY', 'SELL')", name="ck_tx_kind"),
        CheckConstraint("quantity > 0", name="ck_tx_quantity_positive"),
        CheckConstraint("price > 0", name="ck_tx_price_positive"),
    )


class ClosedPositionORM(Base):
    __tablename__ = "closed_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_invested: Mapped[float] = mapped_column(Float, nullable=False)
    total_received: Mapped[float] = mapped_column(Float, nullable=False)
    capital_time_days: Mapped[float] = mapped_column(Float, nullable=False)


# ---- Domain types ------------------------------------------------------------

@dataclass(frozen=True)
class Position:
    """Aktif pozisyonun aggregate görünümü; tx'lerden hesaplanan computed state."""

    symbol: str
    quantity: int
    avg_cost: float
    opened_at: datetime
    updated_at: datetime
    stop_loss: float | None
    target: float | None
    total_invested: float
    total_received: float
    realized_pnl: float
    capital_time_days: float


@dataclass(frozen=True)
class PositionTransaction:
    id: int
    symbol: str
    kind: Kind
    quantity: int
    price: float
    created_at: datetime


@dataclass(frozen=True)
class ClosedPosition:
    """Kapanmış pozisyonun kalıcı arşivi. Computed property'ler stored
    field'lardan türetilir; redundant saklanmaz."""

    id: int
    symbol: str
    opened_at: datetime
    closed_at: datetime
    quantity: int
    total_invested: float
    total_received: float
    capital_time_days: float

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
    def hold_days(self) -> int:
        return (self.closed_at.date() - self.opened_at.date()).days + 1

    @property
    def avg_capital_deployed(self) -> float:
        return self.capital_time_days / self.hold_days

    @property
    def annualized_return_pct(self) -> float | None:
        if self.capital_time_days <= 0:
            return None
        return self.realized_pnl / self.capital_time_days * 365 * 100


# ---- Repository --------------------------------------------------------------

class PortfolioRepository:
    """Portföy + transaction kaydı + kapanmış pozisyon arşivi."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    # ---- yazma -----------------------------------------------------------

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stop_loss: float | None = None,
        target: float | None = None,
    ) -> Position:
        symbol = normalize_symbol(symbol)
        _validate_quantity(quantity)
        validate_price(price, "price")
        _validate_levels(stop_loss, target)

        now = utc_now()
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                session.add(
                    PositionORM(
                        symbol=symbol,
                        stop_loss=stop_loss,
                        target=target,
                        opened_at=now,
                        updated_at=now,
                    )
                )
            else:
                if stop_loss is not None:
                    position.stop_loss = stop_loss
                if target is not None:
                    position.target = target
                position.updated_at = now

            session.add(_new_tx(symbol, "BUY", quantity, price, now))
            session.commit()
            return self._read_position(session, symbol)

    def sell(self, symbol: str, quantity: int, price: float) -> Position | None:
        """SELL kaydı ekler. net_qty=0 olursa pozisyonu kapatır ve
        ``closed_positions``a arşivler; bu durumda None döner."""
        symbol = normalize_symbol(symbol)
        _validate_quantity(quantity)
        validate_price(price, "price")

        now = utc_now()
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                raise KeyError(symbol)

            current_qty = _compute_state(_load_transactions(session, symbol)).net_qty
            if quantity > current_qty:
                raise ValueError(
                    f"Insufficient quantity to sell: have {current_qty}, requested {quantity}."
                )

            session.add(_new_tx(symbol, "SELL", quantity, price, now))
            session.flush()

            if quantity < current_qty:
                position.updated_at = now
                session.commit()
                return self._read_position(session, symbol)

            # Pozisyon kapanıyor: flush sonrası DB'den tekrar yükle (in-memory tx
            # tz-aware, eskiler tz-naive olduğundan karıştırmamak için).
            self._archive(session, position, _load_transactions(session, symbol), now)
            session.execute(delete(PositionTransactionORM).where(PositionTransactionORM.symbol == symbol))
            session.delete(position)
            session.commit()
            return None

    def set_levels(
        self,
        symbol: str,
        stop_loss: float | None = None,
        target: float | None = None,
    ) -> Position:
        """stop_loss ve/veya target günceller; None = bu alana dokunma.
        En az biri verilmeli."""
        if stop_loss is None and target is None:
            raise ValueError("stop_loss veya target'tan en az biri verilmeli.")
        _validate_levels(stop_loss, target)

        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                raise KeyError(symbol)
            if stop_loss is not None:
                position.stop_loss = stop_loss
            if target is not None:
                position.target = target
            position.updated_at = utc_now()
            session.commit()
            return self._read_position(session, symbol)

    def remove(self, symbol: str) -> bool:
        """Tek pozisyonu siler; arşivlenmez (audit trail yok). 'Yanlış girdim'
        senaryosu içindir."""
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                return False
            session.execute(delete(PositionTransactionORM).where(PositionTransactionORM.symbol == symbol))
            session.delete(position)
            session.commit()
            return True

    def clear(self) -> int:
        """Tüm aktif pozisyonları siler; arşivlenmez."""
        with self._session_factory() as session:
            session.execute(delete(PositionTransactionORM))
            count = session.query(PositionORM).delete()
            session.commit()
            return count

    # ---- okuma -----------------------------------------------------------

    def list(self) -> list[Position]:
        with self._session_factory() as session:
            rows = session.execute(select(PositionORM).order_by(PositionORM.symbol)).scalars().all()
            return [self._read_position(session, row.symbol) for row in rows]

    def get(self, symbol: str) -> Position | None:
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            if session.get(PositionORM, symbol) is None:
                return None
            return self._read_position(session, symbol)

    def transactions(self, symbol: str) -> list[PositionTransaction]:
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            rows = _load_transactions(session, symbol)
            return [_to_transaction(r) for r in rows]

    def list_closed(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
    ) -> list[ClosedPosition]:
        with self._session_factory() as session:
            stmt = select(ClosedPositionORM)
            if since is not None:
                stmt = stmt.where(ClosedPositionORM.closed_at >= since)
            if symbol is not None:
                stmt = stmt.where(ClosedPositionORM.symbol == normalize_symbol(symbol))
            stmt = stmt.order_by(ClosedPositionORM.closed_at.desc())
            rows = session.execute(stmt).scalars().all()
            return [_to_closed_position(r) for r in rows]

    # ---- internal --------------------------------------------------------

    def _read_position(self, session: Session, symbol: str) -> Position:
        position = session.get(PositionORM, symbol)
        if position is None:
            raise KeyError(symbol)
        txs = _load_transactions(session, symbol)
        state = _compute_state(txs)
        return Position(
            symbol=symbol,
            quantity=state.net_qty,
            avg_cost=state.avg_cost,
            opened_at=position.opened_at,
            updated_at=position.updated_at,
            stop_loss=position.stop_loss,
            target=position.target,
            total_invested=state.total_invested,
            total_received=state.total_received,
            realized_pnl=state.realized_pnl,
            capital_time_days=_compute_capital_time_days(txs, utc_now().date()),
        )

    def _archive(
        self,
        session: Session,
        position: PositionORM,
        txs: list[PositionTransactionORM],
        closed_at: datetime,
    ) -> None:
        state = _compute_state(txs)
        session.add(
            ClosedPositionORM(
                symbol=position.symbol,
                opened_at=position.opened_at,
                closed_at=closed_at,
                quantity=state.total_bought,
                total_invested=state.total_invested,
                total_received=state.total_received,
                capital_time_days=_compute_capital_time_days(txs, closed_at.date()),
            )
        )


# ---- compute helpers ---------------------------------------------------------

@dataclass(frozen=True)
class _State:
    """Transaction listesinden hesaplanan running state — immutable snapshot."""

    net_qty: int
    total_bought: int
    avg_cost: float
    total_invested: float
    total_received: float
    realized_pnl: float


class _Acc:
    """Average cost yöntemiyle BUY/SELL transitionlarını uygulayan
    mutable accumulator. SELL avg'i değiştirmez, BUY weighted-average yapar."""

    def __init__(self) -> None:
        self.net_qty = 0
        self.total_bought = 0
        self.avg_cost = 0.0
        self.total_invested = 0.0
        self.total_received = 0.0
        self.realized_pnl = 0.0

    def apply_buy(self, quantity: int, price: float) -> None:
        new_qty = self.net_qty + quantity
        self.avg_cost = (self.net_qty * self.avg_cost + quantity * price) / new_qty
        self.net_qty = new_qty
        self.total_bought += quantity
        self.total_invested += quantity * price

    def apply_sell(self, quantity: int, price: float) -> None:
        self.realized_pnl += quantity * (price - self.avg_cost)
        self.net_qty -= quantity
        self.total_received += quantity * price

    def snapshot(self) -> _State:
        return _State(
            net_qty=self.net_qty,
            total_bought=self.total_bought,
            avg_cost=self.avg_cost,
            total_invested=self.total_invested,
            total_received=self.total_received,
            realized_pnl=self.realized_pnl,
        )


def _compute_state(txs: list[PositionTransactionORM]) -> _State:
    """Tüm tx'lere göre nihai state. Sıralama autoincrement id ile (SQLite
    tz-aware/naive karışıklığı olmasın diye datetime yerine id)."""
    acc = _Acc()
    for tx in sorted(txs, key=lambda t: t.id):
        if tx.kind == "BUY":
            acc.apply_buy(tx.quantity, tx.price)
        else:
            acc.apply_sell(tx.quantity, tx.price)
    return acc.snapshot()


def _compute_capital_time_days(
    txs: list[PositionTransactionORM],
    end_date: date,
) -> float:
    """Gün-bazlı, inclusive TL-gün integrali.

    Konvansiyon:
    - BUY günün başında uygulanır (o günden itibaren outstanding artar)
    - SELL günün sonunda uygulanır (o gün hâlâ outstanding sayılır)
    - Pozisyonun yaşadığı her gün ``outstanding × 1`` eklenir
    """
    if not txs:
        return 0.0

    by_date: dict[date, list[PositionTransactionORM]] = defaultdict(list)
    for tx in txs:
        by_date[tx.created_at.date()].append(tx)

    acc = _Acc()
    tl_days = 0.0
    d = min(by_date)
    while d <= end_date:
        for tx in by_date.get(d, []):
            if tx.kind == "BUY":
                acc.apply_buy(tx.quantity, tx.price)
        tl_days += acc.net_qty * acc.avg_cost
        for tx in by_date.get(d, []):
            if tx.kind == "SELL":
                acc.apply_sell(tx.quantity, tx.price)
        d += timedelta(days=1)
    return tl_days


# ---- helpers -----------------------------------------------------------------

def _load_transactions(session: Session, symbol: str) -> list[PositionTransactionORM]:
    return list(
        session.execute(
            select(PositionTransactionORM)
            .where(PositionTransactionORM.symbol == symbol)
            .order_by(PositionTransactionORM.id)
        ).scalars().all()
    )


def _to_transaction(row: PositionTransactionORM) -> PositionTransaction:
    return PositionTransaction(
        id=row.id,
        symbol=row.symbol,
        kind=row.kind,  # type: ignore[arg-type]
        quantity=row.quantity,
        price=row.price,
        created_at=row.created_at,
    )


def _to_closed_position(row: ClosedPositionORM) -> ClosedPosition:
    return ClosedPosition(
        id=row.id,
        symbol=row.symbol,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        quantity=row.quantity,
        total_invested=row.total_invested,
        total_received=row.total_received,
        capital_time_days=row.capital_time_days,
    )


def _validate_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValueError("quantity must be an integer.")
    if quantity <= 0:
        raise ValueError("quantity must be positive.")


def _validate_levels(stop_loss: float | None, target: float | None) -> None:
    if stop_loss is not None:
        validate_price(stop_loss, "stop_loss")
    if target is not None:
        validate_price(target, "target")


def _new_tx(symbol: str, kind: Kind, quantity: int, price: float, when: datetime) -> PositionTransactionORM:
    return PositionTransactionORM(
        symbol=symbol,
        kind=kind,
        quantity=quantity,
        price=price,
        created_at=when,
    )
