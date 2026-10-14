from dataclasses import replace
from datetime import datetime, timezone

from trader_agent.engine.analysis.portfolio import analyze_portfolio
from trader_agent.engine.marketdata.types import PriceQuote
from trader_agent.engine.repository.portfolio import Position


def position(symbol="TEST", quantity=10, cost=90, stop=95):
    return Position("position-id", symbol, quantity, cost, datetime(2026, 1, 1, tzinfo=timezone.utc), stop, 110, "", 0, horizon="swing", source="manual", recommendation_id=None, producer=None, initial_plan={}, plan_changed=False, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def quote(symbol="TEST", price=100):
    return PriceQuote(symbol, price, None, "close" if price is not None else "unavailable")


def test_portfolio_matches_quotes_by_symbol_and_computes_gross_exposure():
    result = analyze_portfolio([position(), position("OTHER", 5, 190, 180)],
                               [quote("OTHER", 200), quote()])
    assert result.market_value == 2000 and result.total_cost == 1850
    assert result.unrealized_pnl == 150
    assert result.planned_downside_to_stops == 150
    assert result.largest_position_weight_pct == 50


def test_missing_price_is_not_zero_exposure():
    result = analyze_portfolio([position()], [quote(price=None)])
    assert result.missing_prices == 1
    assert result.market_value is result.unrealized_pnl is result.planned_downside_to_stops is None
    assert result.largest_position_weight_pct is None and result.total_cost == 900


def test_missing_and_breached_stops_are_not_risk_free():
    for stop in (None, 100, 105):
        result = analyze_portfolio([replace(position(), stop_loss=stop)], [quote()])
        assert result.market_value == 1000
        assert result.planned_downside_to_stops is None
        assert result.missing_stops == int(stop is None)
        assert result.breached_stops == int(stop is not None)


def test_symbol_concentration_combines_independent_positions():
    result = analyze_portfolio([position(quantity=3), position(quantity=4), position("OTHER", 3)], [quote(), quote("OTHER")])
    assert result.largest_position_weight_pct == 70
