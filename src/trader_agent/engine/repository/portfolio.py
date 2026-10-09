from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
from typing import Callable, Literal

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .helpers import normalize_symbol, utc_now, validate_price
from .base import Base

Kind = Literal["BUY", "SELL"]
_MAX_NOTE_LENGTH = 400


class PositionORM(Base):
    """Aktif pozisyonun planı; miktar, maliyet ve tarihler işlemlerden hesaplanır."""

    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String, nullable=False, default="")

    __table_args__ = (
        CheckConstraint("stop_loss IS NULL OR stop_loss > 0", name="ck_position_stop_positive"),
        CheckConstraint("target IS NULL OR target > 0", name="ck_position_target_positive"),
        CheckConstraint("length(note) <= 400", name="ck_position_note_length"),
    )


class PositionTransactionORM(Base):
    """Kapanıştan sonra da korunan alım/satım geçmişi."""

    __tablename__ = "position_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('BUY', 'SELL')", name="ck_tx_kind"),
        CheckConstraint("quantity > 0", name="ck_tx_quantity_positive"),
        CheckConstraint("price > 0", name="ck_tx_price_positive"),
    )


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    avg_cost: float
    opened_at: datetime
    stop_loss: float | None
    target: float | None
    note: str
    realized_pnl: float

    @property
    def hold_days(self) -> int:
        return (utc_now().date() - self.opened_at.date()).days + 1


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
    """İşlemlerden hesaplanan kapanmış pozisyon görünümü."""

    symbol: str
    opened_at: datetime
    closed_at: datetime
    quantity: int
    total_invested: float
    total_received: float

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


class PortfolioRepository:
    """Pozisyon planı + işlem geçmişi. Lot sıfıra indiğinde bir pozisyon dönemi biter."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stop_loss: float | None = None,
        target: float | None = None,
        note: str | None = None,
    ) -> Position:
        symbol = normalize_symbol(symbol)
        _validate_quantity(quantity)
        validate_price(price, "price")
        _validate_plan(stop_loss, target, note)

        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                position = PositionORM(symbol=symbol, note="")
                session.add(position)
            _update_plan(position, stop_loss, target, note)
            session.add(_new_tx(symbol, "BUY", quantity, price))
            session.commit()
            return self._read_position(session, position)

    def sell(self, symbol: str, quantity: int, price: float) -> Position | None:
        """Satışı kaydeder; tam kapanışta işlem geçmişi korunur, aktif plan kaldırılır."""
        symbol = normalize_symbol(symbol)
        _validate_quantity(quantity)
        validate_price(price, "price")

        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                raise KeyError(symbol)
            current_qty = self._read_position(session, position).quantity
            if quantity > current_qty:
                raise ValueError(
                    f"Insufficient quantity to sell: have {current_qty}, requested {quantity}."
                )
            session.add(_new_tx(symbol, "SELL", quantity, price))
            if quantity == current_qty:
                session.delete(position)
                session.commit()
                return None
            session.commit()
            return self._read_position(session, position)

    def set_levels(
        self,
        symbol: str,
        stop_loss: float | None = None,
        target: float | None = None,
        note: str | None = None,
    ) -> Position:
        """Planı günceller. None alanı korur; boş metin notu temizler."""
        if stop_loss is None and target is None and note is None:
            raise ValueError("stop_loss, target veya note alanlarından en az biri verilmeli.")
        _validate_plan(stop_loss, target, note)
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                raise KeyError(symbol)
            _update_plan(position, stop_loss, target, note)
            session.commit()
            return self._read_position(session, position)

    def remove(self, symbol: str) -> bool:
        """Yanlış girilmiş aktif pozisyonu ve o dönemin işlemlerini siler."""
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            if position is None:
                return False
            self._delete_active(session, position)
            session.commit()
            return True

    def clear(self) -> int:
        """Aktif pozisyonları temizler; kapanmış dönemlerin geçmişi korunur."""
        with self._session_factory() as session:
            positions = session.execute(select(PositionORM)).scalars().all()
            for position in positions:
                self._delete_active(session, position)
            session.commit()
            return len(positions)

    def list(self) -> list[Position]:
        with self._session_factory() as session:
            rows = session.execute(select(PositionORM).order_by(PositionORM.symbol)).scalars()
            return [self._read_position(session, row) for row in rows]

    def get(self, symbol: str) -> Position | None:
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            position = session.get(PositionORM, symbol)
            return self._read_position(session, position) if position else None

    def transactions(self, symbol: str) -> list[PositionTransaction]:
        """Sembolün açık ve kapanmış dönemlerindeki tüm işlemlerini döndürür."""
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            return [
                PositionTransaction(
                    id=tx.id,
                    symbol=tx.symbol,
                    kind=tx.kind,  # type: ignore[arg-type]
                    quantity=tx.quantity,
                    price=tx.price,
                    created_at=_as_utc(tx.created_at),
                )
                for tx in _load_transactions(session, symbol)
            ]

    def list_closed(
        self, since: datetime | None = None, symbol: str | None = None
    ) -> list[ClosedPosition]:
        symbol = normalize_symbol(symbol) if symbol is not None else None
        with self._session_factory() as session:
            stmt = select(PositionTransactionORM).order_by(
                PositionTransactionORM.symbol, PositionTransactionORM.id
            )
            if symbol is not None:
                stmt = stmt.where(PositionTransactionORM.symbol == symbol)
            closed = []
            for sym, rows in groupby(session.execute(stmt).scalars(), key=lambda tx: tx.symbol):
                for cycle in _split_cycles(list(rows)):
                    state = _compute_state(cycle)
                    if state.net_qty == 0:
                        closed.append(
                            ClosedPosition(
                                symbol=sym,
                                opened_at=_as_utc(cycle[0].created_at),
                                closed_at=_as_utc(cycle[-1].created_at),
                                quantity=state.total_bought,
                                total_invested=state.total_invested,
                                total_received=state.total_received,
                            )
                        )
            return sorted(
                (
                    item for item in closed
                    if (symbol is None or item.symbol == symbol)
                    and (since is None or item.closed_at >= _as_utc(since))
                ),
                key=lambda item: item.closed_at,
                reverse=True,
            )

    def _read_position(self, session: Session, position: PositionORM) -> Position:
        txs = _split_cycles(_load_transactions(session, position.symbol))[-1]
        state = _compute_state(txs)
        return Position(
            symbol=position.symbol,
            quantity=state.net_qty,
            avg_cost=state.avg_cost,
            opened_at=_as_utc(txs[0].created_at),
            stop_loss=position.stop_loss,
            target=position.target,
            note=position.note,
            realized_pnl=state.realized_pnl,
        )

    def _delete_active(self, session: Session, position: PositionORM) -> None:
        txs = _split_cycles(_load_transactions(session, position.symbol))[-1]
        session.execute(delete(PositionTransactionORM).where(
            PositionTransactionORM.id.in_([tx.id for tx in txs])
        ))
        session.delete(position)


@dataclass(frozen=True)
class _State:
    net_qty: int
    total_bought: int
    avg_cost: float
    total_invested: float
    total_received: float
    realized_pnl: float


def _compute_state(txs: list[PositionTransactionORM]) -> _State:
    quantity = bought = 0
    avg_cost = invested = received = realized = 0.0
    for tx in sorted(txs, key=lambda tx: tx.id):
        if tx.kind == "BUY":
            avg_cost = (quantity * avg_cost + tx.quantity * tx.price) / (quantity + tx.quantity)
            quantity += tx.quantity
            bought += tx.quantity
            invested += tx.quantity * tx.price
        else:
            quantity -= tx.quantity
            received += tx.quantity * tx.price
            realized += tx.quantity * (tx.price - avg_cost)
    return _State(quantity, bought, avg_cost, invested, received, realized)


def _split_cycles(txs: list[PositionTransactionORM]) -> list[list[PositionTransactionORM]]:
    cycles: list[list[PositionTransactionORM]] = []
    current: list[PositionTransactionORM] = []
    quantity = 0
    for tx in txs:
        current.append(tx)
        quantity += tx.quantity if tx.kind == "BUY" else -tx.quantity
        if quantity == 0:
            cycles.append(current)
            current = []
    if current:
        cycles.append(current)
    return cycles


def _load_transactions(session: Session, symbol: str) -> list[PositionTransactionORM]:
    return list(session.execute(
        select(PositionTransactionORM)
        .where(PositionTransactionORM.symbol == symbol)
        .order_by(PositionTransactionORM.id)
    ).scalars())


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _validate_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValueError("quantity must be an integer.")
    if quantity <= 0:
        raise ValueError("quantity must be positive.")


def _validate_plan(stop_loss: float | None, target: float | None, note: str | None) -> None:
    if stop_loss is not None:
        validate_price(stop_loss, "stop_loss")
    if target is not None:
        validate_price(target, "target")
    if note is not None and (not isinstance(note, str) or len(note) > _MAX_NOTE_LENGTH):
        raise ValueError(f"note must be a string of at most {_MAX_NOTE_LENGTH} characters.")


def _update_plan(
    position: PositionORM, stop_loss: float | None, target: float | None, note: str | None
) -> None:
    if stop_loss is not None:
        position.stop_loss = stop_loss
    if target is not None:
        position.target = target
    if note is not None:
        position.note = note


def _new_tx(symbol: str, kind: Kind, quantity: int, price: float) -> PositionTransactionORM:
    return PositionTransactionORM(
        symbol=symbol, kind=kind, quantity=quantity, price=price, created_at=utc_now()
    )
