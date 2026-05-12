from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.repository.base import Base
from trader_agent.repository.portfolio import Position, PositionRepository


@pytest.fixture
def repo() -> PositionRepository:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PositionRepository(session_factory)


# ---- add ---------------------------------------------------------------------

def test_add_new_symbol(repo):
    position, previous = repo.add("THYAO", 100, 285.50)
    assert previous is None
    assert position.symbol == "THYAO"
    assert position.quantity == 100
    assert position.avg_cost == 285.50


def test_add_normalizes_symbol(repo):
    position, _ = repo.add(" thyao ", 50, 100.0)
    assert position.symbol == "THYAO"


def test_add_existing_symbol_computes_weighted_average(repo):
    repo.add("THYAO", 100, 10.0)
    position, previous = repo.add("THYAO", 50, 13.0)

    # (100*10 + 50*13) / 150 = 11
    assert position.quantity == 150
    assert position.avg_cost == pytest.approx(11.0)
    assert previous is not None
    assert previous.quantity == 100
    assert previous.avg_cost == pytest.approx(10.0)


def test_add_rejects_zero_quantity(repo):
    with pytest.raises(ValueError, match="quantity"):
        repo.add("THYAO", 0, 100.0)


def test_add_rejects_negative_quantity(repo):
    with pytest.raises(ValueError, match="quantity"):
        repo.add("THYAO", -10, 100.0)


def test_add_rejects_non_integer_quantity(repo):
    with pytest.raises(ValueError, match="integer"):
        repo.add("THYAO", 10.5, 100.0)  # type: ignore[arg-type]


def test_add_rejects_zero_cost(repo):
    with pytest.raises(ValueError, match="avg_cost"):
        repo.add("THYAO", 10, 0.0)


def test_add_rejects_negative_cost(repo):
    with pytest.raises(ValueError, match="avg_cost"):
        repo.add("THYAO", 10, -1.0)


def test_add_rejects_empty_symbol(repo):
    with pytest.raises(ValueError, match="symbol"):
        repo.add("   ", 10, 100.0)


def test_add_stores_stop_and_target(repo):
    position, _ = repo.add("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    assert position.stop_loss == 90.0
    assert position.target == 120.0


def test_add_preserves_existing_levels_when_omitted(repo):
    repo.add("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    position, _ = repo.add("THYAO", 50, 105.0)
    assert position.stop_loss == 90.0
    assert position.target == 120.0


def test_add_overwrites_levels_when_given(repo):
    repo.add("THYAO", 100, 100.0, stop_loss=90.0)
    position, _ = repo.add("THYAO", 50, 105.0, stop_loss=95.0, target=120.0)
    assert position.stop_loss == 95.0
    assert position.target == 120.0


def test_add_rejects_non_positive_stop(repo):
    with pytest.raises(ValueError, match="stop_loss"):
        repo.add("THYAO", 10, 100.0, stop_loss=0.0)


# ---- set_levels --------------------------------------------------------------

def test_set_levels_updates_only_stop(repo):
    repo.add("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    position = repo.set_levels("THYAO", stop_loss=95.0)
    assert position.stop_loss == 95.0
    assert position.target == 120.0


def test_set_levels_updates_only_target(repo):
    repo.add("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    position = repo.set_levels("THYAO", target=125.0)
    assert position.stop_loss == 90.0
    assert position.target == 125.0


def test_set_levels_requires_at_least_one(repo):
    repo.add("THYAO", 100, 100.0)
    with pytest.raises(ValueError):
        repo.set_levels("THYAO")


def test_set_levels_missing_position_raises(repo):
    with pytest.raises(KeyError):
        repo.set_levels("THYAO", stop_loss=10.0)


# ---- remove ------------------------------------------------------------------

def test_remove_existing_returns_true(repo):
    repo.add("THYAO", 10, 100.0)
    assert repo.remove("THYAO") is True
    assert repo.list() == []


def test_remove_normalizes_symbol(repo):
    repo.add("THYAO", 10, 100.0)
    assert repo.remove(" thyao ") is True


def test_remove_missing_returns_false(repo):
    assert repo.remove("THYAO") is False


# ---- clear -------------------------------------------------------------------

def test_clear_returns_deleted_count(repo):
    repo.add("THYAO", 10, 100.0)
    repo.add("AKBNK", 20, 50.0)
    assert repo.clear() == 2
    assert repo.list() == []


def test_clear_empty_returns_zero(repo):
    assert repo.clear() == 0


# ---- list --------------------------------------------------------------------

def test_list_returns_positions_sorted_by_symbol(repo):
    repo.add("THYAO", 10, 100.0)
    repo.add("AKBNK", 20, 50.0)
    repo.add("GARAN", 30, 75.0)

    symbols = [p.symbol for p in repo.list()]
    assert symbols == ["AKBNK", "GARAN", "THYAO"]


def test_list_empty_returns_empty(repo):
    assert repo.list() == []


def test_list_returns_position_dtos(repo):
    repo.add("THYAO", 10, 100.0)
    positions = repo.list()
    assert len(positions) == 1
    assert isinstance(positions[0], Position)
