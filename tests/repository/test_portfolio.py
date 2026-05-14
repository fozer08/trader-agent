from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.repository.base import Base
from trader_agent.repository.portfolio import (
    PortfolioRepository,
    Position,
    PositionTransactionORM,
    _compute_capital_time_days,
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
    assert position.total_invested == pytest.approx(100 * 10 + 50 * 13)


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


def test_sell_full_closes_and_archives(repo):
    repo.buy("THYAO", 100, 10.0)
    result = repo.sell("THYAO", 100, 12.0)
    assert result is None  # kapandı
    assert repo.get("THYAO") is None

    closed = repo.list_closed()
    assert len(closed) == 1
    assert closed[0].symbol == "THYAO"
    assert closed[0].quantity == 100
    assert closed[0].realized_pnl == pytest.approx(200.0)


def test_sell_more_than_available_raises(repo):
    repo.buy("THYAO", 100, 10.0)
    with pytest.raises(ValueError, match="Insufficient"):
        repo.sell("THYAO", 150, 12.0)


def test_sell_missing_position_raises(repo):
    with pytest.raises(KeyError):
        repo.sell("THYAO", 10, 12.0)


def test_sell_partial_then_full_archives_combined(repo):
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


def test_capital_time_days_simple():
    """BUY May 1, SELL May 3 → 3 inclusive gün × 1000 = 3000."""
    txs = [_tx("BUY", 100, 10.0, 1), _tx("SELL", 100, 11.0, 3)]
    result = _compute_capital_time_days(txs, txs[-1].created_at.date())
    assert result == pytest.approx(3000.0)


def test_capital_time_days_same_day():
    """Aynı gün açıp kapatma → 1 gün × 1000 = 1000."""
    txs = [_tx("BUY", 100, 10.0, 1), _tx("SELL", 100, 11.0, 1)]
    result = _compute_capital_time_days(txs, txs[-1].created_at.date())
    assert result == pytest.approx(1000.0)


def test_capital_time_days_multi_buy():
    """May 1 BUY 100@10, May 3 BUY 100@14, May 5 SELL 200 close.
    Beklenen: May 1-2 (1000×2) + May 3-5 (2400×3) = 2000 + 7200 = 9200."""
    txs = [
        _tx("BUY", 100, 10.0, 1),
        _tx("BUY", 100, 14.0, 3),
        _tx("SELL", 200, 16.0, 5),
    ]
    result = _compute_capital_time_days(txs, txs[-1].created_at.date())
    assert result == pytest.approx(9200.0)


def test_capital_time_days_active_position_uses_end_date():
    """Aktif pozisyon: end_date=today olduğunda integralin orana göre artması."""
    txs = [_tx("BUY", 100, 10.0, 1)]  # May 1
    # May 1'den May 10'a kadar 10 gün outstanding 1000
    result = _compute_capital_time_days(txs, datetime(2026, 5, 10).date())
    assert result == pytest.approx(10 * 1000.0)


def test_capital_time_days_partial_sell_then_close():
    """May 1 BUY 100@10, May 2 SELL 50@12, May 4 SELL 50 close.
    Outstanding'ler:
      May 1 (buy): qty 100, avg 10 → 1000 (saysın)
      May 2 (sell): pre-sell 1000 saysın, sonra qty 50, avg 10 → 500
      May 3: 500
      May 4 (sell, close): pre-sell 500 saysın, sonra qty 0
    Toplam: 1000 + 1000 + 500 + 500 = 3000.
    """
    txs = [
        _tx("BUY", 100, 10.0, 1),
        _tx("SELL", 50, 12.0, 2),
        _tx("SELL", 50, 13.0, 4),
    ]
    result = _compute_capital_time_days(txs, txs[-1].created_at.date())
    assert result == pytest.approx(3000.0)


def test_capital_time_days_archived_on_close(repo):
    """Repository sell ile pozisyon kapatınca capital_time_days arşivlenir."""
    # Aynı gün BUY ve SELL → 1 gün × 1000
    repo.buy("THYAO", 100, 10.0)
    repo.sell("THYAO", 100, 12.0)
    closed = repo.list_closed()[0]
    # capital_time_days hesaplandı; pozitif olmalı
    assert closed.capital_time_days > 0


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
    assert c.avg_capital_deployed == pytest.approx(c.capital_time_days / c.hold_days)
    annualized = c.annualized_return_pct
    assert annualized is not None
    assert annualized == pytest.approx(c.realized_pnl / c.capital_time_days * 365 * 100)


# ---- Position type check -----------------------------------------------------

def test_list_returns_position_dtos(repo):
    repo.buy("THYAO", 10, 100.0)
    positions = repo.list()
    assert len(positions) == 1
    assert isinstance(positions[0], Position)
