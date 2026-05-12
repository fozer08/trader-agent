from __future__ import annotations

from pathlib import Path
from typing import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .base import Base


def create_db_engine(path: Path) -> Engine:
    """SQLite engine üretir. Tek dosyalık DB, dosya yoksa açılışta yaratılır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", future=True)


def create_session_factory(engine: Engine) -> Callable[[], Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_schema(engine: Engine) -> None:
    """ORM tablolarını yaratır. Migration yok — schema değişirse DB dosyasını sil."""
    from . import portfolio, recommendations  # noqa: F401

    Base.metadata.create_all(engine)
