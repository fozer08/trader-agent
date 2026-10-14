from __future__ import annotations

from dataclasses import dataclass

from ..marketdata.types import PriceQuote
from ..positions import Position


@dataclass(frozen=True)
class PortfolioExposure:
    """Kayıtlı long pozisyonların ortak agent/web özetidir; nakit hesabı değildir.

    Toplamlar eksik fiyat veya geçersiz/eksik stop varsa None kalır. Stop riski
    güncel fiyattan planlanan stopa mesafedir; başlangıç riski veya azami kayıp değildir.
    """

    total_cost: float
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    realized_pnl: float
    missing_prices: int
    missing_stops: int
    breached_stops: int
    planned_downside_to_stops: float | None
    largest_position_weight_pct: float | None


def analyze_portfolio(positions: list[Position], quotes: list[PriceQuote]) -> PortfolioExposure:
    """Pozisyonları sembolle fiyatlar; bilinmeyen maruziyeti sıfır saymaz."""
    by_symbol = {quote.symbol: quote.price for quote in quotes}
    cost = sum(p.quantity * p.avg_cost for p in positions)
    missing_prices = sum(by_symbol.get(p.symbol) is None for p in positions)
    missing_stops = sum(p.stop_loss is None for p in positions)
    values: list[float] = []
    symbol_values: dict[str, float] = {}
    downside = 0.0
    breached = 0
    for position in positions:
        price = by_symbol.get(position.symbol)
        if price is None:
            continue
        values.append(position.quantity * price)
        symbol_values[position.symbol] = symbol_values.get(position.symbol, 0) + position.quantity * price
        if position.stop_loss is not None:
            if price <= position.stop_loss:
                breached += 1
            else:
                downside += position.quantity * (price - position.stop_loss)
    value = None if missing_prices else sum(values)
    pnl = None if value is None else value - cost
    return PortfolioExposure(
        total_cost=cost, market_value=value, unrealized_pnl=pnl,
        unrealized_pnl_pct=None if pnl is None or cost == 0 else pnl / cost * 100,
        realized_pnl=sum(p.realized_pnl for p in positions), missing_prices=missing_prices,
        missing_stops=missing_stops, breached_stops=breached,
        planned_downside_to_stops=None if missing_prices or missing_stops or breached else downside,
        largest_position_weight_pct=max(symbol_values.values()) / value * 100 if value else None,
    )
