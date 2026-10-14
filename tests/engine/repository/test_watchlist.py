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


def test_new_account_starts_with_an_empty_watchlist(storage):
    repository, factory, _ = storage
    assert repository.list() == []
    with factory() as session:
        assert list(session.scalars(select(WatchlistORM.symbol))) == []


def test_existing_watchlist_is_kept_and_survives_repository_restart(storage):
    repository, factory, stocks = storage
    repository.add("S11")
    reopened = WatchlistRepository(factory, stocks)

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


def test_empty_list_stays_empty_after_restart(storage):
    repository, factory, stocks = storage
    repository.add("S11")
    assert repository.remove(" s11 ")
    assert not repository.remove("S11")
    assert repository.list() == []

    assert WatchlistRepository(factory, stocks).list() == []


@pytest.mark.parametrize("size", [0, 3])
def test_small_catalog_does_not_preselect_stocks(storage, size):
    _, factory, stocks = storage
    repository = WatchlistRepository(factory, stocks[:size])
    assert repository.list() == []
    assert repository.stocklist() == stocks[:size]


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

def test_user_lists_are_isolated_and_limit_is_atomic(storage):
    from concurrent.futures import ThreadPoolExecutor

    _, factory, stocks = storage
    first = WatchlistRepository(factory, stocks, "alice")
    second = WatchlistRepository(factory, stocks, "bob")
    for stock in stocks[:9]:
        first.add(stock["symbol"])

    def add(symbol):
        try:
            first.add(symbol)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add, ["S09", "S10"]))
    assert sorted(results) == [False, True]
    assert len(first.list()) == 10
    assert second.list() == []
    first.add("S00")  # A repeated selection does not consume a slot.
    second.add("S11")
    first.remove("S00")
    first.add("S11")
    assert second.list() == [stocks[11]]
    assert len(first.list()) == 10
