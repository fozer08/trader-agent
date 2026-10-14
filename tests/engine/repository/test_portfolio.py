from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from trader_agent.engine.repository import create_db_engine, init_schema, create_session_factory
from trader_agent.engine.repository.portfolio import PortfolioRepository
from trader_agent.engine.repository.recommendations import RecommendationRepository

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


@pytest.fixture
def stores(tmp_path):
    engine = create_db_engine(tmp_path / "portfolio.db")
    init_schema(engine)
    factory = create_session_factory(engine)
    clock = [NOW]
    repo = PortfolioRepository(factory, "alice", lambda: clock[0])
    recs = RecommendationRepository(factory, "alice")
    yield repo, recs, clock, factory
    engine.dispose()


def recommendation(recs, **values):
    return recs.publish(
        **dict(
            symbol="THYAO",
            entry_min=100.0,
            entry_max=105.0,
            stop_loss=95.0,
            target=120.0,
            horizon="swing",
            rationale="Trend takibi",
            risk_level="moderate",
            producer="assistant",
            producer_version="test-v1",
            at=NOW,
            expires_at=NOW + timedelta(hours=9),
            data_as_of=NOW - timedelta(minutes=15),
            **values,
        )
    )


@pytest.mark.parametrize("horizon", ["intraday", "swing", "position"])
def test_independent_positions_and_explicit_additions(stores, horizon):
    repo, _, _, _ = stores
    first = repo.buy(" thyao ", 100, 10, horizon=horizon)
    second = repo.buy("THYAO", 50, 13, horizon="position")
    assert first.id != second.id
    assert len(repo.list()) == 2
    added = repo.buy("THYAO", 50, 13, position_id=first.id)
    assert added.quantity == 150 and added.avg_cost == 11
    assert added.horizon == horizon
    with pytest.raises(ValueError, match="mevcut vadeyi"):
        repo.buy(
            "THYAO",
            1,
            10,
            position_id=first.id,
            horizon="intraday" if horizon != "intraday" else "swing",
        )
    assert repo.get(second.id).quantity == 50
    assert [t.quantity for t in repo.transactions(first.id)] == [100, 50]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(quantity=0),
        dict(quantity=-1),
        dict(quantity=True),
        dict(quantity=1.2),
        dict(price=0),
        dict(price=float("nan")),
        dict(price=float("inf")),
        dict(horizon=None),
        dict(horizon="forever"),
        dict(note="x" * 401),
        dict(symbol=""),
        dict(stop_loss=-1),
        dict(target=0),
        dict(stop_loss=110, target=100),
    ],
)
def test_invalid_manual_input_writes_nothing(stores, kwargs):
    repo, _, _, _ = stores
    args = dict(symbol="THYAO", quantity=10, price=100, horizon="swing")
    args.update(kwargs)
    with pytest.raises(ValueError):
        repo.buy(**args)
    assert repo.list() == []


def test_partial_full_close_average_cost_and_reopening(stores):
    repo, _, clock, _ = stores
    p = repo.buy("THYAO", 100, 10, horizon="swing", opened_at=NOW - timedelta(days=2))
    repo.sell(p.id, 40, 12)
    repo.buy("THYAO", 40, 15, position_id=p.id)
    assert repo.get(p.id).avg_cost == 12
    clock[0] += timedelta(days=1)
    assert repo.sell(p.id, 100, 13) is None
    assert repo.list() == []
    closed = repo.list_closed()[0]
    assert closed.realized_pnl == 180
    assert closed.quantity == 140 and closed.total_invested == 1600
    assert closed.hold_days == 4
    assert repo.get(p.id).closed_at is not None
    newer = repo.buy("THYAO", 10, 20, horizon="position")
    repo.sell(newer.id, 10, 18)
    assert newer.id != p.id
    assert repo.performance()["realized_pnl"] == 160
    assert len(repo.transactions(p.id)) == 4
    assert len(repo.transactions(newer.id)) == 2


def test_oversell_and_unknown_ids_preserve_history(stores):
    repo, _, _, _ = stores
    p = repo.buy("THYAO", 10, 100, horizon="swing")
    with pytest.raises(ValueError, match="fazla"):
        repo.sell(p.id, 11, 110)
    with pytest.raises(KeyError):
        repo.sell("missing", 1, 100)
    assert len(repo.transactions(p.id)) == 1
    assert repo.get(p.id).quantity == 10


def test_plan_revisions_preserve_initial_conditions(stores):
    repo, recs, _, _ = stores
    r = recommendation(recs)
    p = repo.accept(r.id, 10, 102)
    repo.set_levels(p.id, stop_loss=99, horizon="position", note="Yeni plan")
    repo.set_levels(p.id, note="")
    changed = repo.get(p.id)
    assert changed.initial_plan["stop_loss"] == 95
    assert changed.initial_plan["horizon"] == "swing"
    assert changed.source == "system" and changed.plan_changed and changed.note == ""
    assert [p["note"] for p in repo.plans(p.id)] == ["Yeni plan", ""]
    assert len(repo.transactions(p.id)) == 1
    with pytest.raises(ValueError):
        repo.set_levels(p.id, stop_loss=130)
    assert repo.get(p.id).stop_loss == 99
    assert len(repo.plans(p.id)) == 2


def test_suggestion_and_manual_same_symbol_never_merge(stores):
    repo, recs, _, _ = stores
    r = recommendation(recs)
    manual = repo.buy("THYAO", 20, 110, horizon="position")
    system = repo.accept(r.id, 10, 102)
    assert len(repo.list()) == 2
    assert repo.list(source="manual")[0].id == manual.id
    assert repo.list(source="system")[0].id == system.id
    assert system.initial_plan["entry_min"] == 100
    assert recs.get(r.id, NOW).position_id == system.id
    repo.sell(manual.id, 20, 100)
    repo.sell(system.id, 10, 110)
    assert repo.performance("manual")["realized_pnl"] == -200
    assert repo.performance("system")["realized_pnl"] == 80
    assert repo.performance()["realized_pnl"] == -120


@pytest.mark.parametrize("price", [99.99, 105.01])
def test_range_is_enforced_for_conversion_and_addition(stores, price):
    repo, recs, _, _ = stores
    r = recommendation(recs)
    with pytest.raises(ValueError, match="aralığında"):
        repo.accept(r.id, 10, price)
    assert repo.list() == []
    p = repo.accept(r.id, 10, 100)
    with pytest.raises(ValueError, match="aralığında"):
        repo.buy("THYAO", 10, price, position_id=p.id)
    assert repo.get(p.id).quantity == 10


def test_expiry_hides_recommendation_but_never_closes_position(stores):
    repo, recs, clock, _ = stores
    r = recommendation(recs)
    p = repo.accept(r.id, 10, 105)
    clock[0] = r.expires_at
    assert recs.list(clock[0]) == []
    assert not recs.get(r.id, clock[0]).active
    assert repo.get(p.id).quantity == 10
    with pytest.raises(ValueError, match="süresi"):
        repo.buy("THYAO", 1, 102, position_id=p.id)
    repo.sell(p.id, 10, 110)
    assert repo.list_closed()[0].source == "system"


def test_expired_conversion_and_changed_idempotency_request_are_rejected(stores):
    repo, recs, clock, _ = stores
    r = recommendation(recs)
    p = repo.accept(r.id, 10, 102, "request-1")
    clock[0] = r.expires_at
    assert repo.accept(r.id, 10, 102, "request-1").id == p.id
    with pytest.raises(ValueError, match="farklı"):
        repo.accept(r.id, 20, 102, "request-1")
    with pytest.raises(ValueError, match="zaten"):
        repo.accept(r.id, 10, 102, "request-2")
    other = RecommendationRepository(repo._session_factory, "other")
    r2 = recommendation(other)
    other_portfolio = PortfolioRepository(repo._session_factory, "other", lambda: clock[0])
    with pytest.raises(ValueError, match="süresi"):
        other_portfolio.accept(r2.id, 10, 102)


def test_parallel_accept_is_atomic_and_retry_does_not_duplicate(stores):
    repo, recs, _, _ = stores
    r = recommendation(recs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        positions = list(pool.map(lambda _: repo.accept(r.id, 10, 102, "one"), range(2)))
    assert positions[0].id == positions[1].id
    assert len(repo.list()) == len(repo.transactions(positions[0].id)) == 1


def test_manual_and_close_retries_are_idempotent(stores):
    repo, _, _, _ = stores
    p = repo.buy("THYAO", 10, 100, horizon="swing", operation_id="buy")
    assert repo.buy("THYAO", 10, 100, horizon="swing", operation_id="buy").id == p.id
    repo.sell(p.id, 10, 120, operation_id="close")
    assert repo.sell(p.id, 10, 120, operation_id="close") is None
    assert len(repo.transactions(p.id)) == 2


def test_cross_user_reads_and_writes_fail(stores):
    repo, recs, _, factory = stores
    other = PortfolioRepository(factory, "bob", lambda: NOW)
    r = recommendation(recs)
    p = repo.accept(r.id, 10, 102)
    assert other.get(p.id) is None
    for call in [
        lambda: other.transactions(p.id),
        lambda: other.plans(p.id),
        lambda: other.sell(p.id, 1, 100),
        lambda: other.accept(r.id, 10, 102),
        lambda: other.set_levels(p.id, target=130),
    ]:
        with pytest.raises(KeyError):
            call()
    assert other.remove(p.id) is False
    assert other.performance()["realized_pnl"] == 0


def test_performance_period_uses_partial_close_time_and_counts_only_full_closes(stores):
    repo, _, clock, _ = stores
    p = repo.buy("THYAO", 10, 100, horizon="swing")
    repo.sell(p.id, 2, 120)
    assert repo.performance()["realized_pnl"] == 40
    assert repo.performance()["closed_count"] == 0
    assert repo.performance()["win_rate"] is None
    clock[0] += timedelta(days=10)
    repo.sell(p.id, 8, 90)
    period = repo.performance(since=NOW + timedelta(days=1))
    assert period["realized_pnl"] == -80 and period["released_cost"] == 800
    assert period["realized_return_pct"] == -10
    assert period["closed_count"] == period["losses"] == 1
    assert period["average_hold_days"] is None
    assert repo.performance()["realized_pnl"] == -40
    assert repo.performance(until=NOW + timedelta(days=1))["realized_pnl"] == 40


def test_voiding_open_records_does_not_erase_closed_history(stores):
    repo, _, _, factory = stores
    p = repo.buy("THYAO", 10, 100, horizon="swing")
    repo.sell(p.id, 10, 120)
    newer = repo.buy("THYAO", 10, 100, horizon="swing")
    repo.sell(newer.id, 1, 110)
    assert repo.remove(newer.id)
    assert not repo.remove(newer.id)
    assert repo.get(newer.id) is None
    assert repo.performance()["realized_pnl"] == 200
    restarted = PortfolioRepository(factory, "alice")
    assert len(restarted.list_closed()) == 1
    assert len(restarted.transactions(p.id)) == 2


def test_date_validation_and_unknown_date(stores):
    repo, _, _, _ = stores
    assert repo.buy("THYAO", 10, 100, horizon="swing").opened_at is None
    for opened_at in [NOW + timedelta(days=1), NOW.replace(tzinfo=None)]:
        with pytest.raises(ValueError):
            repo.buy("THYAO", 10, 100, horizon="swing", opened_at=opened_at)
    assert len(repo.list()) == 1


def test_performance_period_uses_instants_across_timezone_offsets(stores):
    repo, _, _, _ = stores
    position = repo.buy("THYAO", 10, 100, horizon="swing")
    repo.sell(position.id, 10, 110)
    local_start = (NOW - timedelta(minutes=1)).astimezone(timezone(timedelta(hours=3)))
    local_end = (NOW + timedelta(minutes=1)).astimezone(timezone(timedelta(hours=3)))
    result = repo.performance(since=local_start, until=local_end)
    assert result["realized_pnl"] == 100
    assert result["closed_count"] == 1
    assert repo.performance(until=local_start)["closed_count"] == 0
    assert repo.performance(since=local_end)["realized_pnl"] == 0
