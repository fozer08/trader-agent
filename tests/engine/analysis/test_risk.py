import pytest

from trader_agent.engine.analysis import evaluate_trade


def test_gross_risk_is_explicit_and_does_not_invent_quantity():
    risk = evaluate_trade(100, 95, 110)
    assert risk.risk_per_share == 5 and risk.reward_per_share == 10
    assert risk.stop_distance_pct == 5 and risk.target_distance_pct == 10
    assert risk.reward_to_risk == 2
    assert risk.net_reward_to_risk is risk.max_quantity is risk.planned_risk_amount is None


def test_costs_and_both_budgets_limit_lots():
    risk = evaluate_trade(100, 95, 110, risk_budget=60, cash_budget=505, round_trip_cost_pct=1)
    assert risk.net_reward_to_risk == 1.5
    assert risk.max_quantity == 5 and risk.notional == 500 and risk.planned_risk_amount == 30


def test_small_budget_returns_zero_lots_and_no_arbitrary_rr_filter():
    risk = evaluate_trade(100, 90, 101, risk_budget=9)
    assert risk.max_quantity == 0 and risk.reward_to_risk == 0.1


@pytest.mark.parametrize("values", [
    (0, 90, 110), (100, 100, 110), (100, 105, 110), (100, 90, 90),
    (float("nan"), 90, 110), (100, float("inf"), 110), (True, .5, 2),
])
def test_invalid_plan_is_rejected(values):
    with pytest.raises(ValueError):
        evaluate_trade(*values)


@pytest.mark.parametrize("options", [
    {"risk_budget": 0}, {"cash_budget": -1}, {"round_trip_cost_pct": -1},
    {"round_trip_cost_pct": float("inf")}, {"risk_budget": True},
])
def test_invalid_budgets_and_costs_are_rejected(options):
    with pytest.raises(ValueError):
        evaluate_trade(100, 95, 110, **options)


def test_decimal_price_step_does_not_lose_a_lot_to_float_rounding():
    risk = evaluate_trade(1.1, 1., 1.3, risk_budget=1.)
    assert risk.max_quantity == 10
