from __future__ import annotations

import builtins
import hashlib
import json
from datetime import datetime
from typing import Callable
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base
from .helpers import normalize_symbol, utc_now, validate_price
from .recommendations import RecommendationORM, aware, recommendation_view
from ..analysis import Horizon
from ..positions import (
    ClosedPosition,
    Kind,
    Position,
    PositionTransaction,
    Source,
    compute_state,
    summarize_performance,
)


class PositionORM(Base):
    """Kimlikli plan; kapandıktan sonra kaynak ve ilk öneri koşulları korunur."""

    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    horizon: Mapped[Horizon] = mapped_column(String)
    source: Mapped[Source] = mapped_column(String)
    producer: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    initial_plan: Mapped[dict] = mapped_column(JSON)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("user_id", "recommendation_id"),
        CheckConstraint("source IN ('manual','system')"),
        CheckConstraint("horizon IN ('intraday','swing','position')"),
    )


class PositionTransactionORM(Base):
    """Tek pozisyonun miktar hareketi ve kapanan maliyeti; kısmi sonuçları korur."""

    __tablename__ = "position_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    symbol: Mapped[str] = mapped_column(String)
    kind: Mapped[Kind] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    released_cost: Mapped[float] = mapped_column(Float, default=0)
    operation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String)
    __table_args__ = (
        UniqueConstraint("user_id", "operation_id"),
        CheckConstraint("kind IN ('BUY','SELL')"),
        CheckConstraint("quantity > 0"),
        CheckConstraint("price > 0"),
    )


class PositionPlanORM(Base):
    """Plan değişikliğinin zamanlı kopyası; ilk planın üzerine yazılmasını önler."""

    __tablename__ = "position_plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    plan: Mapped[dict] = mapped_column(JSON)


class PortfolioRepository:
    """Kullanıcıya ait bağımsız pozisyonları atomik hareketlerle tutar ve kaynak sınırlarını denetler."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        user_id: str = "system",
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.user_id = user_id
        self._session_factory = session_factory
        self._now = now

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stop_loss: float | None = None,
        target: float | None = None,
        note: str | None = None,
        horizon: Horizon | None = None,
        *,
        position_id: str | None = None,
        opened_at: datetime | None = None,
        operation_id: str | None = None,
    ) -> Position:
        symbol = normalize_symbol(symbol)
        _validate_quantity(quantity)
        validate_price(price, "price")
        _validate_plan(stop_loss, target, note, horizon)
        now = aware(self._now())
        if opened_at is not None and (opened_at.tzinfo is None or opened_at > now):
            raise ValueError("Pozisyon tarihi saat dilimli olmalı ve gelecekte olamaz.")
        fingerprint = _fingerprint(
            "BUY",
            symbol,
            quantity,
            price,
            stop_loss,
            target,
            note,
            horizon,
            position_id,
            opened_at.isoformat() if opened_at else None,
        )
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            replay = self._replay(session, operation_id, fingerprint)
            if replay:
                return self._read_position(session, replay)
            if position_id:
                row = self._require(session, position_id, active=True)
                if row.symbol != symbol:
                    raise ValueError("Hisse pozisyonla eşleşmiyor.")
                if horizon is not None and horizon != row.horizon:
                    raise ValueError("Ek alım mevcut vadeyi korur; planı ayrıca düzenleyin.")
                if any(x is not None for x in (stop_loss, target, note, opened_at)):
                    raise ValueError(
                        "Miktar artırırken planı değiştirmeyin; planı ayrıca düzenleyin."
                    )
                if row.recommendation_id:
                    self._eligible_recommendation(session, row.recommendation_id, price, now)
            else:
                if horizon is None:
                    raise ValueError("Yeni pozisyon için horizon (vade) belirtilmeli.")
                plan = dict(stop_loss=stop_loss, target=target, horizon=horizon, note=note or "")
                row = PositionORM(
                    id=str(uuid4()),
                    user_id=self.user_id,
                    symbol=symbol,
                    horizon=horizon,
                    source="manual",
                    initial_plan=plan,
                    stop_loss=stop_loss,
                    target=target,
                    note=note or "",
                    created_at=now,
                    opened_at=aware(opened_at) if opened_at else None,
                    voided=False,
                )
                session.add(row)
            self._add_tx(session, row, "BUY", quantity, price, now, operation_id, fingerprint)
            session.commit()
            return self._read_position(session, row)

    def accept(
        self, recommendation_id: str, quantity: int, price: float, operation_id: str | None = None
    ) -> Position:
        _validate_quantity(quantity)
        validate_price(price, "price")
        now = aware(self._now())
        fingerprint = _fingerprint("ACCEPT", recommendation_id, quantity, price)
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            replay = self._replay(session, operation_id, fingerprint)
            if replay:
                return self._read_position(session, replay)
            existing = session.scalar(
                select(PositionORM).where(
                    PositionORM.user_id == self.user_id,
                    PositionORM.recommendation_id == recommendation_id,
                )
            )
            if existing:
                raise ValueError("Bu öneri zaten portföye eklendi; mevcut pozisyonu açın.")
            rec = self._eligible_recommendation(session, recommendation_id, price, now)
            row = PositionORM(
                id=str(uuid4()),
                user_id=self.user_id,
                symbol=rec.symbol,
                horizon=rec.horizon,
                source="system",
                producer=rec.producer,
                recommendation_id=rec.id,
                initial_plan=recommendation_view(rec, now).to_dict(),
                stop_loss=rec.stop_loss,
                target=rec.target,
                note=rec.rationale,
                created_at=now,
                opened_at=now,
                voided=False,
            )
            session.add(row)
            self._add_tx(session, row, "BUY", quantity, price, now, operation_id, fingerprint)
            session.commit()
            return self._read_position(session, row)

    def sell(
        self, position_id: str, quantity: int, price: float, *, operation_id: str | None = None
    ) -> Position | None:
        _validate_quantity(quantity)
        validate_price(price, "price")
        fingerprint = _fingerprint("SELL", position_id, quantity, price)
        now = aware(self._now())
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            replay = self._replay(session, operation_id, fingerprint)
            if replay:
                return None if replay.closed_at else self._read_position(session, replay)
            row = self._require(session, position_id, active=True)
            state = compute_state(_load_transactions(session, position_id, self.user_id))
            if quantity > state.net_qty:
                raise ValueError("Pozisyon miktarından fazla kapatılamaz.")
            self._add_tx(
                session,
                row,
                "SELL",
                quantity,
                price,
                now,
                operation_id,
                fingerprint,
                realized_pnl=quantity * (price - state.avg_cost),
                released_cost=quantity * state.avg_cost,
            )
            if quantity == state.net_qty:
                row.closed_at = now
            session.commit()
            return None if row.closed_at else self._read_position(session, row)

    def set_levels(
        self,
        position_id: str,
        stop_loss: float | None = None,
        target: float | None = None,
        note: str | None = None,
        horizon: Horizon | None = None,
    ) -> Position:
        if all(x is None for x in (stop_loss, target, note, horizon)):
            raise ValueError("Planın en az bir alanını belirtin.")
        _validate_plan(stop_loss, target, note, horizon)
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self._require(session, position_id, active=True)
            for key, value in (
                ("stop_loss", stop_loss),
                ("target", target),
                ("note", note),
                ("horizon", horizon),
            ):
                if value is not None:
                    setattr(row, key, value)
            if row.stop_loss and row.target and row.stop_loss >= row.target:
                raise ValueError("Stop hedefin altında olmalı.")
            session.add(
                PositionPlanORM(
                    user_id=self.user_id,
                    position_id=row.id,
                    created_at=aware(self._now()),
                    plan=dict(
                        stop_loss=row.stop_loss,
                        target=row.target,
                        horizon=row.horizon,
                        note=row.note,
                    ),
                )
            )
            session.commit()
            return self._read_position(session, row)

    def remove(self, position_id: str) -> bool:
        """Hatalı açık kaydı iptal eder; geçmiş denetim için korunur, performansa girmez."""
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                row = self._require(session, position_id, active=True)
            except KeyError:
                return False
            row.voided = True
            session.commit()
            return True

    def clear(self) -> int:
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            rows = session.scalars(
                select(PositionORM).where(
                    PositionORM.user_id == self.user_id,
                    PositionORM.closed_at.is_(None),
                    PositionORM.voided.is_(False),
                )
            ).all()
            for row in rows:
                row.voided = True
            session.commit()
            return len(rows)

    def list(self, *, source: Source | None = None) -> list[Position]:
        with self._session_factory() as session:
            stmt = select(PositionORM).where(
                PositionORM.user_id == self.user_id,
                PositionORM.closed_at.is_(None),
                PositionORM.voided.is_(False),
            )
            if source:
                stmt = stmt.where(PositionORM.source == source)
            return [
                self._read_position(session, row)
                for row in session.scalars(
                    stmt.order_by(PositionORM.symbol, PositionORM.created_at)
                )
            ]

    def get(self, position_id: str) -> Position | None:
        with self._session_factory() as session:
            try:
                return self._read_position(session, self._require(session, position_id))
            except KeyError:
                return None

    def transactions(self, position_id: str) -> builtins.list[PositionTransaction]:
        with self._session_factory() as session:
            self._require(session, position_id)
            return [
                PositionTransaction(
                    id=t.id,
                    position_id=t.position_id,
                    symbol=t.symbol,
                    kind=t.kind,
                    quantity=t.quantity,
                    price=t.price,
                    created_at=aware(t.created_at),
                    realized_pnl=t.realized_pnl,
                    released_cost=t.released_cost,
                )
                for t in _load_transactions(session, position_id, self.user_id)
            ]

    def plans(self, position_id: str) -> builtins.list[dict]:
        with self._session_factory() as session:
            self._require(session, position_id)
            rows = session.scalars(
                select(PositionPlanORM)
                .where(
                    PositionPlanORM.user_id == self.user_id,
                    PositionPlanORM.position_id == position_id,
                )
                .order_by(PositionPlanORM.id)
            )
            return [{"created_at": aware(row.created_at).isoformat(), **row.plan} for row in rows]

    def list_closed(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        source: Source | None = None,
        until: datetime | None = None,
    ) -> builtins.list[ClosedPosition]:
        with self._session_factory() as session:
            stmt = select(PositionORM).where(
                PositionORM.user_id == self.user_id,
                PositionORM.closed_at.is_not(None),
                PositionORM.voided.is_(False),
            )
            if symbol:
                stmt = stmt.where(PositionORM.symbol == normalize_symbol(symbol))
            if source:
                stmt = stmt.where(PositionORM.source == source)
            if since:
                stmt = stmt.where(PositionORM.closed_at >= aware(since))
            if until:
                stmt = stmt.where(PositionORM.closed_at < aware(until))
            result = []
            for row in session.scalars(stmt.order_by(PositionORM.closed_at.desc())):
                state = compute_state(_load_transactions(session, row.id, self.user_id))
                assert row.closed_at is not None
                result.append(
                    ClosedPosition(
                        row.id,
                        row.symbol,
                        aware(row.opened_at) if row.opened_at else None,
                        aware(row.closed_at),
                        state.total_bought,
                        state.total_invested,
                        state.total_received,
                        row.source,
                        row.producer,
                        row.horizon,
                        row.recommendation_id,
                    )
                )
            return result

    def performance(
        self,
        source: Source | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict:
        """Kapanış tarihine göre kısmi hareketleri ve tam kapanan planları ayrı toplar."""
        closed = self.list_closed(source=source, since=since, until=until)
        with self._session_factory() as session:
            stmt = (
                select(PositionTransactionORM)
                .join(PositionORM, PositionORM.id == PositionTransactionORM.position_id)
                .where(
                    PositionORM.user_id == self.user_id,
                    PositionORM.voided.is_(False),
                    PositionTransactionORM.kind == "SELL",
                )
            )
            if source:
                stmt = stmt.where(PositionORM.source == source)
            if since:
                stmt = stmt.where(PositionTransactionORM.created_at >= aware(since))
            if until:
                stmt = stmt.where(PositionTransactionORM.created_at < aware(until))
            trades = session.scalars(stmt).all()
            realized = [(t.realized_pnl, t.released_cost) for t in trades]
        return summarize_performance(closed, realized)

    def _require(self, session: Session, position_id: str, *, active: bool = False) -> PositionORM:
        row = session.get(PositionORM, position_id)
        if (
            row is None
            or row.user_id != self.user_id
            or row.voided
            or (active and row.closed_at is not None)
        ):
            raise KeyError("Pozisyon bulunamadı.")
        return row

    def _eligible_recommendation(
        self, session: Session, recommendation_id: str, price: float, now: datetime
    ) -> RecommendationORM:
        row = session.get(RecommendationORM, recommendation_id)
        if row is None or row.user_id != self.user_id:
            raise KeyError("Öneri bulunamadı.")
        if row.withdrawn or aware(row.expires_at) <= now:
            raise ValueError("Bu önerinin süresi doldu. Yeni önerileri inceleyin.")
        if not row.entry_min <= price <= row.entry_max:
            raise ValueError(
                f"Giriş fiyatı {row.entry_min:g}–{row.entry_max:g} TL aralığında olmalı."
            )
        return row

    def _replay(
        self, session: Session, operation_id: str | None, fingerprint: str
    ) -> PositionORM | None:
        if not operation_id:
            return None
        if not isinstance(operation_id, str) or len(operation_id) > 100:
            raise ValueError("Geçersiz işlem kimliği.")
        tx = session.scalar(
            select(PositionTransactionORM).where(
                PositionTransactionORM.user_id == self.user_id,
                PositionTransactionORM.operation_id == operation_id,
            )
        )
        if tx:
            if tx.fingerprint != fingerprint:
                raise ValueError("İşlem kimliği farklı bir kayıt için kullanılmış.")
            return self._require(session, tx.position_id)
        return None

    def _add_tx(
        self,
        session: Session,
        row: PositionORM,
        kind: Kind,
        quantity: int,
        price: float,
        at: datetime,
        operation_id: str | None,
        fingerprint: str,
        realized_pnl: float = 0,
        released_cost: float = 0,
    ) -> None:
        session.add(
            PositionTransactionORM(
                position_id=row.id,
                user_id=self.user_id,
                symbol=row.symbol,
                kind=kind,
                quantity=quantity,
                price=price,
                created_at=at,
                operation_id=operation_id,
                fingerprint=fingerprint,
                realized_pnl=realized_pnl,
                released_cost=released_cost,
            )
        )
        session.flush()

    def _read_position(self, session: Session, row: PositionORM) -> Position:
        state = compute_state(_load_transactions(session, row.id, self.user_id))
        changed = (
            session.scalar(
                select(PositionPlanORM.id)
                .where(
                    PositionPlanORM.user_id == self.user_id, PositionPlanORM.position_id == row.id
                )
                .limit(1)
            )
            is not None
        )
        return Position(
            row.id,
            row.symbol,
            state.net_qty,
            state.avg_cost,
            aware(row.opened_at) if row.opened_at else None,
            row.stop_loss,
            row.target,
            row.note,
            state.realized_pnl,
            row.horizon,
            row.source,
            row.recommendation_id,
            row.producer,
            row.initial_plan,
            changed,
            aware(row.created_at),
            aware(row.closed_at) if row.closed_at else None,
        )


def _load_transactions(
    session: Session, position_id: str, user_id: str
) -> list[PositionTransactionORM]:
    return list(
        session.scalars(
            select(PositionTransactionORM)
            .where(
                PositionTransactionORM.user_id == user_id,
                PositionTransactionORM.position_id == position_id,
            )
            .order_by(PositionTransactionORM.id)
        )
    )


def _fingerprint(*values: object) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _validate_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValueError("quantity must be an integer.")
    if quantity <= 0:
        raise ValueError("quantity must be positive.")


def _validate_plan(
    stop_loss: float | None, target: float | None, note: str | None, horizon: Horizon | None
) -> None:
    if stop_loss is not None:
        validate_price(stop_loss, "stop_loss")
    if target is not None:
        validate_price(target, "target")
    if stop_loss is not None and target is not None and stop_loss >= target:
        raise ValueError("Stop hedefin altında olmalı.")
    if note is not None and (not isinstance(note, str) or len(note) > 400):
        raise ValueError("note en fazla 400 karakter olmalı.")
    if horizon is not None and horizon not in ("intraday", "swing", "position"):
        raise ValueError("horizon must be intraday, swing or position.")
