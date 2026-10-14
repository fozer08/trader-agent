from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Literal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base
from .helpers import normalize_symbol, validate_price
from ..analysis import Horizon, RiskLevel

Producer = Literal["assistant", "model"]


class RecommendationORM(Base):
    """Günlük önerinin değişmez koşulları; sonuç veya başarı hesabı saklamaz."""

    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    fingerprint: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    entry_min: Mapped[float] = mapped_column(Float)
    entry_max: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    horizon: Mapped[Horizon] = mapped_column(String)
    risk_level: Mapped[RiskLevel | None] = mapped_column(String, nullable=True)
    rationale: Mapped[str] = mapped_column(String)
    producer: Mapped[Producer] = mapped_column(String)
    producer_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("user_id", "fingerprint"),)


@dataclass(frozen=True)
class Recommendation:
    """UI, model ve pozisyonun paylaştığı doğrulanmış günlük öneri görünümü."""

    id: str
    symbol: str
    entry_min: float
    entry_max: float
    stop_loss: float
    target: float
    horizon: Horizon
    risk_level: RiskLevel | None
    rationale: str
    producer: Producer
    producer_version: str
    created_at: datetime
    expires_at: datetime
    data_as_of: datetime
    active: bool
    position_id: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("created_at", "expires_at", "data_as_of"):
            data[key] = data[key].isoformat()
        return data


def aware(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def recommendation_view(
    row: RecommendationORM, at: datetime, position_id: str | None = None
) -> Recommendation:
    return Recommendation(
        id=row.id,
        symbol=row.symbol,
        entry_min=row.entry_min,
        entry_max=row.entry_max,
        stop_loss=row.stop_loss,
        target=row.target,
        horizon=row.horizon,
        risk_level=row.risk_level,
        rationale=row.rationale,
        producer=row.producer,
        producer_version=row.producer_version,
        created_at=aware(row.created_at),
        expires_at=aware(row.expires_at),
        data_as_of=aware(row.data_as_of),
        active=not row.withdrawn and aware(row.expires_at) > at,
        position_id=position_id,
    )


def validate_recommendation(
    symbol: str,
    entry_min: float,
    entry_max: float,
    stop_loss: float,
    target: float,
    horizon: Horizon,
    rationale: str,
    risk_level: RiskLevel | None,
) -> str:
    symbol = normalize_symbol(symbol)
    for key, value in (
        ("entry_min", entry_min),
        ("entry_max", entry_max),
        ("stop_loss", stop_loss),
        ("target", target),
    ):
        validate_price(value, key)
    if not stop_loss < entry_min <= entry_max < target:
        raise ValueError("Stop < giriş alt sınırı ≤ giriş üst sınırı < hedef olmalı.")
    if horizon not in ("intraday", "swing", "position"):
        raise ValueError("Geçersiz horizon.")
    if risk_level not in (None, "low", "moderate", "high"):
        raise ValueError("Geçersiz risk seviyesi.")
    if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 400:
        raise ValueError("Öneri gerekçesi 1–400 karakter olmalı.")
    return symbol


class RecommendationRepository:
    """Kullanıcının günlük önerilerini tekilleştirir; süreyi sorgu anında denetler."""

    def __init__(self, session_factory: Callable[[], Session], user_id: str = "system") -> None:
        self._session_factory = session_factory
        self.user_id = user_id

    def publish(
        self,
        *,
        symbol: str,
        entry_min: float,
        entry_max: float,
        stop_loss: float,
        target: float,
        horizon: Horizon,
        rationale: str,
        risk_level: RiskLevel | None,
        producer: Producer,
        producer_version: str,
        at: datetime,
        expires_at: datetime,
        data_as_of: datetime,
    ) -> Recommendation:
        symbol = validate_recommendation(
            symbol, entry_min, entry_max, stop_loss, target, horizon, rationale, risk_level
        )
        if at.tzinfo is None or expires_at.tzinfo is None or data_as_of.tzinfo is None:
            raise ValueError("Öneri zamanlarında saat dilimi gerekli.")
        if expires_at <= at or data_as_of > at:
            raise ValueError("Önerinin geçerlilik veya veri zamanı uygun değil.")
        if producer not in ("assistant", "model") or not producer_version.strip():
            raise ValueError("Öneri üreticisi ve sürümü gerekli.")
        at, expires_at, data_as_of = aware(at), aware(expires_at), aware(data_as_of)
        key = json.dumps(
            [
                symbol,
                float(entry_min),
                float(entry_max),
                float(stop_loss),
                float(target),
                horizon,
                risk_level,
                producer,
                producer_version,
                expires_at.isoformat(),
            ],
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(key.encode()).hexdigest()
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(
                select(RecommendationORM).where(
                    RecommendationORM.user_id == self.user_id,
                    RecommendationORM.fingerprint == fingerprint,
                )
            )
            if row is None:
                row = RecommendationORM(
                    id=str(uuid4()),
                    user_id=self.user_id,
                    fingerprint=fingerprint,
                    symbol=symbol,
                    entry_min=entry_min,
                    entry_max=entry_max,
                    stop_loss=stop_loss,
                    target=target,
                    horizon=horizon,
                    risk_level=risk_level,
                    rationale=rationale.strip(),
                    producer=producer,
                    producer_version=producer_version,
                    created_at=at,
                    expires_at=expires_at,
                    data_as_of=data_as_of,
                    withdrawn=False,
                )
                session.add(row)
                session.commit()
            return recommendation_view(row, at)

    def list(self, at: datetime, *, active_only: bool = True) -> list[Recommendation]:
        from .portfolio import PositionORM

        with self._session_factory() as session:
            stmt = (
                select(RecommendationORM, PositionORM.id)
                .outerjoin(
                    PositionORM,
                    (PositionORM.recommendation_id == RecommendationORM.id)
                    & (PositionORM.user_id == self.user_id),
                )
                .where(RecommendationORM.user_id == self.user_id)
            )
            if active_only:
                stmt = stmt.where(
                    RecommendationORM.expires_at > aware(at), RecommendationORM.withdrawn.is_(False)
                )
            return [
                recommendation_view(row, at, position_id)
                for row, position_id in session.execute(
                    stmt.order_by(RecommendationORM.created_at.desc())
                )
            ]

    def get(self, recommendation_id: str, at: datetime) -> Recommendation:
        from .portfolio import PositionORM

        with self._session_factory() as session:
            row = session.get(RecommendationORM, recommendation_id)
            if row is None or row.user_id != self.user_id:
                raise KeyError("Öneri bulunamadı.")
            position_id = session.scalar(
                select(PositionORM.id).where(
                    PositionORM.user_id == self.user_id, PositionORM.recommendation_id == row.id
                )
            )
            return recommendation_view(row, at, position_id)
