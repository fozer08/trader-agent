from __future__ import annotations

import pytest
from sqlalchemy import select

from trader_agent.engine.repository import (
    WatchlistORM,
    WatchlistRepository,
    create_db_engine,
    create_session_factory,
    init_schema,
)


@pytest.fixture
def storage(tmp_path):
    database = create_db_engine(tmp_path / "data.db")
    init_schema(database)
    factory = create_session_factory(database)
    stocks = [{"symbol": f"S{i:02}", "name": f"Şirket {i}"} for i in range(12)]
    yield WatchlistRepository(factory, stocks), factory, stocks
    database.dispose()


def test_seed_uses_first_ten_stocks_in_file_order(storage):
    repository, factory, stocks = storage
    repository.seed_if_empty()

    assert repository.list() == stocks[:10]
    with factory() as session:
        assert list(session.scalars(select(WatchlistORM.symbol).order_by(WatchlistORM.id))) == [
            stock["symbol"] for stock in stocks[:10]
        ]


def test_existing_watchlist_is_kept_and_survives_repository_restart(storage):
    repository, factory, stocks = storage
    repository.add("S11")
    repository.seed_if_empty()
    reopened = WatchlistRepository(factory, stocks)
    reopened.seed_if_empty()

    assert reopened.list() == [stocks[11]]


def test_names_come_from_catalog_and_selection_is_persisted(storage):
    repository, factory, stocks = storage
    repository.add("S11")
    updated_stocks = [dict(stock) for stock in stocks]
    updated_stocks[11]["name"] = "Yeni şirket adı"

    assert WatchlistRepository(factory, updated_stocks).list() == [updated_stocks[11]]


def test_add_normalizes_symbol_and_is_idempotent(storage):
    repository, _, stocks = storage
    repository.add(" s11 ")
    repository.add("S11")

    assert repository.list() == [stocks[11]]
    assert repository.stocklist() == stocks


def test_only_catalog_stocks_can_be_added(storage):
    repository, _, _ = storage
    with pytest.raises(ValueError, match="temel listede bulunamadı"):
        repository.add("UNKNOWN")

    assert repository.list() == []


def test_remove_leaves_empty_list_until_next_startup(storage):
    repository, _, stocks = storage
    repository.add("S11")
    assert repository.remove(" s11 ")
    assert not repository.remove("S11")
    assert repository.list() == []

    repository.seed_if_empty()
    assert repository.list() == stocks[:10]


@pytest.mark.parametrize("size", [0, 3])
def test_small_catalog_seeds_all_available_stocks(storage, size):
    _, factory, stocks = storage
    repository = WatchlistRepository(factory, stocks[:size])
    repository.seed_if_empty()
    assert repository.list() == stocks[:size]


@pytest.mark.parametrize("stocks", [
    {},
    ["THYAO"],
    [{"name": "Eksik sembol"}],
    [{"symbol": ""}],
    [{"symbol": "THYAO", "name": ""}],
    [{"symbol": "THYAO"}, {"symbol": "thyao"}],
])
def test_invalid_catalog_is_rejected(storage, stocks):
    _, factory, _ = storage
    with pytest.raises(ValueError):
        WatchlistRepository(factory, stocks)
