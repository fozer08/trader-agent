"""Trading tercihleri ekran ayarlarından ayrı olarak engine'e aittir."""
from typing import Callable

from sqlalchemy import String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base
from ..agent.preferences import ConversationPreferences
from ..analysis import Horizon, RiskLevel


class TradingPreferencesORM(Base):
    """Kullanıcının varsayılan değerlendirme vadesini ve risk seviyesini saklar."""

    __tablename__ = "trading_preferences"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    horizon: Mapped[Horizon | None] = mapped_column(String, nullable=True)
    risk_level: Mapped[RiskLevel | None] = mapped_column(String, nullable=True)


class TradingPreferencesRepository:
    """Trading profilini yalnız bağlı olduğu kullanıcı için okur ve günceller."""

    def __init__(self, factory: Callable[[], Session], user_id: str) -> None:
        self.factory = factory
        self.user_id = user_id

    def get(self) -> ConversationPreferences:
        with self.factory() as session:
            row = session.get(TradingPreferencesORM, self.user_id)
            return ConversationPreferences(row.horizon, row.risk_level) if row else ConversationPreferences()

    def save(self, value: ConversationPreferences) -> ConversationPreferences:
        with self.factory() as session:
            session.merge(TradingPreferencesORM(user_id=self.user_id, horizon=value.horizon,
                                                risk_level=value.risk_level))
            session.commit()
        return value
