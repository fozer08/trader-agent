"""Engine'in sınırlı karar hafızası; tam sohbet arşivi web backend'ine aittir."""
from typing import Callable

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .base import Base


class ConversationORM(Base):
    """Kullanıcıyla adlandırılmış sohbetin doğrulanabilir engine hafızasını saklar."""

    __tablename__ = "conversation_memory"
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class ConversationRepository:
    """Tur bitiminde sınırlı hafızayı atomik kaydeder; restart sonrası geri yükler."""

    def __init__(self, factory: Callable[[], Session]) -> None:
        self.factory = factory

    def get(self, session_id: str) -> dict | None:
        with self.factory() as session:
            row = session.get(ConversationORM, session_id)
            return row.snapshot if row else None

    def save(self, session_id: str, snapshot: dict) -> None:
        with self.factory() as session:
            session.merge(ConversationORM(session_id=session_id, snapshot=snapshot))
            session.commit()
