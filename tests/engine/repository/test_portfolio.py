from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.engine.repository.base import Base
from trader_agent.engine.repository.portfolio import (
    PortfolioRepository,
    Position,
    PositionTransactionORM,
    _compute_state,
)


@pytest.fixture
def repo() -> PortfolioRepository:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PortfolioRepository(session_factory)


# ---- buy ---------------------------------------------------------------------

def test_buy_new_symbol(repo):
    position = repo.buy("THYAO", 100, 285.50)
    assert position.symbol == "THYAO"
    assert position.quantity == 100
    assert position.avg_cost == pytest.approx(285.50)
    assert position.stop_loss is None
    assert position.target is None


def test_buy_normalizes_symbol(repo):
    position = repo.buy(" thyao ", 50, 100.0)
    assert position.symbol == "THYAO"


def test_buy_weighted_average(repo):
    repo.buy("THYAO", 100, 10.0)
    position = repo.buy("THYAO", 50, 13.0)
    # (100*10 + 50*13) / 150 = 11
    assert position.quantity == 150
    assert position.avg_cost == pytest.approx(11.0)
    assert position.quantity * position.avg_cost == pytest.approx(100 * 10 + 50 * 13)


def test_buy_stores_levels(repo):
    position = repo.buy("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    assert position.stop_loss == 90.0
    assert position.target == 120.0


def test_buy_preserves_levels_when_omitted(repo):
    repo.buy("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    position = repo.buy("THYAO", 50, 105.0)
    assert position.stop_loss == 90.0
    assert position.target == 120.0


def test_buy_overwrites_levels_when_given(repo):
    repo.buy("THYAO", 100, 100.0, stop_loss=90.0)
    position = repo.buy("THYAO", 50, 105.0, stop_loss=95.0, target=120.0)
    assert position.stop_loss == 95.0
    assert position.target == 120.0


def test_buy_rejects_zero_quantity(repo):
    with pytest.raises(ValueError, match="quantity"):
        repo.buy("THYAO", 0, 100.0)


def test_buy_rejects_negative_quantity(repo):
    with pytest.raises(ValueError, match="quantity"):
        repo.buy("THYAO", -10, 100.0)


def test_buy_rejects_non_integer_quantity(repo):
    with pytest.raises(ValueError, match="integer"):
        repo.buy("THYAO", 10.5, 100.0)  # type: ignore[arg-type]


def test_buy_rejects_zero_price(repo):
    with pytest.raises(ValueError, match="price"):
        repo.buy("THYAO", 10, 0.0)


def test_buy_rejects_empty_symbol(repo):
    with pytest.raises(ValueError, match="symbol"):
        repo.buy("   ", 10, 100.0)


# ---- sell --------------------------------------------------------------------

def test_sell_partial_keeps_position(repo):
    repo.buy("THYAO", 100, 10.0)
    position = repo.sell("THYAO", 30, 12.0)
    assert position is not None
    assert position.quantity == 70
    assert position.avg_cost == pytest.approx(10.0)  # SELL avg'i değiştirmez
    assert position.realized_pnl == pytest.approx(30 * (12.0 - 10.0))


def test_sell_full_closes_and_preserves_transactions(repo):
    repo.buy("THYAO", 100, 10.0)
    result = repo.sell("THYAO", 100, 12.0)
    assert result is None  # kapandı
    assert repo.get("THYAO") is None

    closed = repo.list_closed()
    assert len(closed) == 1
    assert closed[0].symbol == "THYAO"
    assert closed[0].quantity == 100
    assert closed[0].realized_pnl == pytest.approx(200.0)
    assert [tx.kind for tx in repo.transactions("THYAO")] == ["BUY", "SELL"]


def test_sell_more_than_available_raises(repo):
    repo.buy("THYAO", 100, 10.0)
    with pytest.raises(ValueError, match="Insufficient"):
        repo.sell("THYAO", 150, 12.0)


def test_sell_missing_position_raises(repo):
    with pytest.raises(KeyError):
        repo.sell("THYAO", 10, 12.0)


def test_sell_partial_then_full_combines_transactions(repo):
    repo.buy("THYAO", 100, 10.0)
    repo.sell("THYAO", 40, 11.0)
    result = repo.sell("THYAO", 60, 12.0)
    assert result is None

    closed = repo.list_closed()
    assert len(closed) == 1
    c = closed[0]
    # invested = 100 * 10 = 1000; received = 40*11 + 60*12 = 1160
    assert c.total_invested == pytest.approx(1000.0)
    assert c.total_received == pytest.approx(1160.0)
    assert c.realized_pnl == pytest.approx(160.0)


# ---- set_levels --------------------------------------------------------------

def test_set_levels_updates_stop_only(repo):
    repo.buy("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    position = repo.set_levels("THYAO", stop_loss=95.0)
    assert position.stop_loss == 95.0
    assert position.target == 120.0


def test_set_levels_requires_at_least_one(repo):
    repo.buy("THYAO", 100, 100.0)
    with pytest.raises(ValueError):
        repo.set_levels("THYAO")


def test_set_levels_missing_position_raises(repo):
    with pytest.raises(KeyError):
        repo.set_levels("THYAO", stop_loss=10.0)


def test_set_levels_adds_to_position_without_levels(repo):
    repo.buy("THYAO", 100, 100.0)  # no levels
    position = repo.set_levels("THYAO", stop_loss=90.0, target=120.0)
    assert position.stop_loss == 90.0
    assert position.target == 120.0


# ---- remove / clear ----------------------------------------------------------

def test_remove_existing_returns_true_no_archive(repo):
    repo.buy("THYAO", 10, 100.0)
    assert repo.remove("THYAO") is True
    assert repo.list() == []
    assert repo.list_closed() == []  # arşivlenmez


def test_remove_missing_returns_false(repo):
    assert repo.remove("THYAO") is False


def test_clear_returns_count_no_archive(repo):
    repo.buy("THYAO", 10, 100.0)
    repo.buy("AKBNK", 20, 50.0)
    assert repo.clear() == 2
    assert repo.list() == []
    assert repo.list_closed() == []


# ---- list / get / transactions -----------------------------------------------

def test_list_sorted_by_symbol(repo):
    repo.buy("THYAO", 10, 100.0)
    repo.buy("AKBNK", 20, 50.0)
    repo.buy("GARAN", 30, 75.0)
    symbols = [p.symbol for p in repo.list()]
    assert symbols == ["AKBNK", "GARAN", "THYAO"]


def test_get_returns_aggregated(repo):
    repo.buy("THYAO", 100, 10.0)
    repo.buy("THYAO", 100, 14.0)
    position = repo.get("THYAO")
    assert position is not None
    assert position.quantity == 200
    assert position.avg_cost == pytest.approx(12.0)


def test_get_missing_returns_none(repo):
    assert repo.get("THYAO") is None


def test_transactions_returns_in_order(repo):
    repo.buy("THYAO", 100, 10.0)
    repo.sell("THYAO", 30, 11.0)
    repo.buy("THYAO", 50, 12.0)
    txs = repo.transactions("THYAO")
    assert [t.kind for t in txs] == ["BUY", "SELL", "BUY"]
    assert [t.quantity for t in txs] == [100, 30, 50]


# ---- list_closed -------------------------------------------------------------

def test_list_closed_filters_by_symbol(repo):
    repo.buy("THYAO", 10, 100.0)
    repo.sell("THYAO", 10, 110.0)
    repo.buy("AKBNK", 20, 50.0)
    repo.sell("AKBNK", 20, 55.0)

    only_thyao = repo.list_closed(symbol="THYAO")
    assert len(only_thyao) == 1
    assert only_thyao[0].symbol == "THYAO"


def test_list_closed_filters_by_since(repo):
    repo.buy("THYAO", 10, 100.0)
    repo.sell("THYAO", 10, 110.0)
    # Hemen sonrasını filtrelersek hâlâ görünmeli (kapanış anlık)
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert repo.list_closed(since=future) == []


# ---- compute helpers (algorithm) ---------------------------------------------

def _tx(kind: str, quantity: int, price: float, day: int) -> PositionTransactionORM:
    """Test helper: günü ay 5'in `day`'i olarak ayarlar."""
    return PositionTransactionORM(
        id=day,
        symbol="X",
        kind=kind,
        quantity=quantity,
        price=price,
        created_at=datetime(2026, 5, day, 12, 0, tzinfo=timezone.utc),
    )


def test_state_avg_cost_method():
    """BUY weighted-average, SELL avg'i değiştirmez."""
    txs = [
        _tx("BUY", 100, 10.0, 1),
        _tx("BUY", 100, 14.0, 2),
        _tx("SELL", 50, 15.0, 3),
    ]
    state = _compute_state(txs)
    assert state.net_qty == 150
    assert state.total_bought == 200
    assert state.avg_cost == pytest.approx(12.0)
    assert state.total_invested == pytest.approx(2400.0)
    assert state.total_received == pytest.approx(50 * 15.0)
    assert state.realized_pnl == pytest.approx(50 * (15.0 - 12.0))


# ---- ClosedPosition computed properties --------------------------------------

def test_closed_position_computed_properties(repo):
    repo.buy("THYAO", 100, 10.0)
    repo.sell("THYAO", 100, 12.0)
    c = repo.list_closed()[0]
    assert c.realized_pnl == pytest.approx(200.0)
    assert c.realized_pnl_pct == pytest.approx(20.0)
    assert c.avg_buy_cost == pytest.approx(10.0)
    assert c.avg_sell_price == pytest.approx(12.0)
    assert c.hold_days >= 1


# ---- Position type check -----------------------------------------------------

def test_list_returns_position_dtos(repo):
    repo.buy("THYAO", 10, 100.0)
    positions = repo.list()
    assert len(positions) == 1
    assert isinstance(positions[0], Position)


def test_reopen_starts_new_cost_basis_and_preserves_closed_history(repo):
    repo.buy("THYAO", 100, 10.0, stop_loss=9.0, target=12.0, note="İlk pozisyon")
    repo.sell("THYAO", 100, 12.0)
    position = repo.buy("THYAO", 20, 30.0)

    assert position.quantity == 20
    assert position.avg_cost == pytest.approx(30.0)
    assert position.realized_pnl == 0.0
    assert position.stop_loss is None
    assert position.target is None
    assert position.note == ""
    assert len(repo.transactions("THYAO")) == 3
    assert repo.list_closed()[0].realized_pnl == pytest.approx(200.0)


def test_multiple_cycles_and_partial_sales_are_calculated_separately(repo):
    repo.buy("THYAO", 100, 10.0)
    repo.sell("THYAO", 40, 12.0)
    repo.buy("THYAO", 40, 15.0)  # Yeni ortalama: (60*10 + 40*15) / 100 = 12
    repo.sell("THYAO", 100, 13.0)
    repo.buy("THYAO", 10, 20.0)
    repo.sell("THYAO", 10, 18.0)

    closed = repo.list_closed(symbol=" thyao ")
    assert [item.realized_pnl for item in closed] == pytest.approx([-20.0, 180.0])
    assert [item.quantity for item in closed] == [10, 140]
    assert len(repo.transactions("THYAO")) == 6
    assert repo.list() == []


@pytest.mark.parametrize("operation", ["remove", "clear"])
def test_clearing_active_position_preserves_previous_cycles(repo, operation):
    repo.buy("THYAO", 10, 10.0)
    repo.sell("THYAO", 10, 12.0)
    repo.buy("THYAO", 20, 30.0)

    if operation == "remove":
        assert repo.remove("THYAO") is True
    else:
        assert repo.clear() == 1

    assert repo.get("THYAO") is None
    assert [tx.kind for tx in repo.transactions("THYAO")] == ["BUY", "SELL"]
    assert repo.list_closed()[0].realized_pnl == pytest.approx(20.0)


def test_plan_note_can_be_updated_and_cleared_without_changing_trades(repo):
    repo.buy("THYAO", 100, 10.0, stop_loss=9.0, target=12.0, note="Destek takibi")
    repo.buy("THYAO", 10, 11.0)
    assert repo.get("THYAO").note == "Destek takibi"

    position = repo.set_levels("THYAO", note="Koşul değişti")
    assert position.note == "Koşul değişti"
    assert position.stop_loss == 9.0
    assert position.target == 12.0
    assert len(repo.transactions("THYAO")) == 2
    assert repo.set_levels("THYAO", note="").note == ""


def test_invalid_note_does_not_create_a_trade(repo):
    with pytest.raises(ValueError, match="note"):
        repo.buy("THYAO", 100, 10.0, note="x" * 401)
    assert repo.list() == []
    assert repo.transactions("THYAO") == []


def test_history_survives_repository_restart(repo):
    repo.buy("THYAO", 10, 10.0)
    repo.sell("THYAO", 10, 12.0)
    restarted = PortfolioRepository(repo._session_factory)
    assert restarted.list() == []
    assert len(restarted.transactions("THYAO")) == 2
    assert restarted.list_closed()[0].realized_pnl == pytest.approx(20.0)
