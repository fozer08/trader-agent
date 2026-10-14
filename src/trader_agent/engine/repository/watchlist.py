from __future__ import annotations

from typing import Callable

from sqlalchemy import Integer, String, UniqueConstraint, delete, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .helpers import normalize_symbol
from .base import Base


class WatchlistORM(Base):
    """Takip edilen semboller; şirket bilgileri temel hisse listesinden gelir."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "symbol"),)


class WatchlistRepository:
    """Kullanıcının en fazla on hisselik listesini atomik olarak saklar."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        stocklist: list[dict],
        user_id: str = "system",
    ) -> None:
        self._session_factory = session_factory
        self.user_id = user_id
        self._stocks: dict[str, dict] = {}
        if not isinstance(stocklist, list):
            raise ValueError("Stocklist JSON bir hisse listesi olmalıdır.")
        for entry in stocklist:
            if not isinstance(entry, dict) or not isinstance(entry.get("symbol"), str):
                raise ValueError("Her stocklist kaydı bir symbol içermelidir.")
            symbol = normalize_symbol(entry["symbol"])
            name = entry.get("name", symbol)
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Hisse adı boş olamaz: {symbol}")
            if symbol in self._stocks:
                raise ValueError(f"Stocklist içinde tekrarlanan sembol: {symbol}")
            self._stocks[symbol] = {"symbol": symbol, "name": name.strip()}

    def stocklist(self) -> list[dict]:
        """Seçilebilir tüm hisseler, dosyadaki sıralarıyla."""
        return [dict(entry) for entry in self._stocks.values()]

    def stock(self, symbol: str) -> dict:
        symbol = normalize_symbol(symbol)
        if symbol not in self._stocks:
            raise KeyError(f"Hisse temel listede bulunamadı: {symbol}")
        return dict(self._stocks[symbol])

    def list(self) -> list[dict]:
        """Tablodaki hisseler, eklenme sıralarıyla ve temel listedeki adlarıyla."""
        with self._session_factory() as session:
            symbols = session.scalars(select(WatchlistORM.symbol).where(WatchlistORM.user_id == self.user_id).order_by(WatchlistORM.id))
            return [dict(self._stocks[symbol]) for symbol in symbols if symbol in self._stocks]

    def add(self, symbol: str) -> dict:
        try:
            entry = self.stock(symbol)
        except KeyError as exc:
            raise ValueError(str(exc.args[0])) from exc
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            symbols = set(session.scalars(select(WatchlistORM.symbol).where(
                WatchlistORM.user_id == self.user_id)))
            if entry["symbol"] not in symbols and len(symbols) >= 10:
                raise ValueError("En fazla 10 hisse takip edebilirsiniz.")
            session.execute(
                insert(WatchlistORM).values(symbol=entry["symbol"], user_id=self.user_id)
                .on_conflict_do_nothing(index_elements=["user_id", "symbol"])
            )
            session.commit()
        return entry

    def remove(self, symbol: str) -> bool:
        symbol = normalize_symbol(symbol)
        with self._session_factory() as session:
            removed = session.scalar(delete(WatchlistORM).where(WatchlistORM.symbol == symbol, WatchlistORM.user_id == self.user_id).returning(WatchlistORM.symbol))
            session.commit()
            return removed is not None
