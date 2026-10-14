from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TradeRisk:
    """Yeni long planının sayısal riskini taşır; uygunluk veya emir kararı vermez.

    Stop kaybı planlanan fiyat üzerinden hesaplanır, gerçekleşme garantisi
    değildir. Maliyet verilmediyse sonuç brüttür; bilinmeyen maliyet sıfır sayılmaz.
    """

    entry: float
    stop: float
    target: float
    risk_per_share: float
    reward_per_share: float
    stop_distance_pct: float
    target_distance_pct: float
    reward_to_risk: float
    round_trip_cost_pct: float | None
    net_reward_to_risk: float | None
    max_quantity: int | None
    planned_risk_amount: float | None
    notional: float | None


def evaluate_trade(
    entry: float, stop: float, target: float, *,
    risk_budget: float | None = None, cash_budget: float | None = None,
    round_trip_cost_pct: float | None = None,
) -> TradeRisk:
    """Stop < giriş < hedef long planını hesaplar; verilen bütçelerle tam lot sınırlar.

    Maliyet iki çıkışta da giriş nominalinin aynı yüzdesi varsayılır; gerçek
    komisyon/vergi modeli değildir. Bütçeler yoksa lot veya hesap yüzdesi uydurmaz.
    """
    values = (entry, stop, target, risk_budget, cash_budget)
    if any(v is not None and (isinstance(v, bool) or not math.isfinite(v) or v <= 0) for v in values):
        raise ValueError("Prices and supplied budgets must be finite and positive.")
    if not stop < entry < target:
        raise ValueError("A new long plan requires stop < entry < target.")
    if round_trip_cost_pct is not None and (
        isinstance(round_trip_cost_pct, bool) or not math.isfinite(round_trip_cost_pct)
        or round_trip_cost_pct < 0
    ):
        raise ValueError("Round-trip cost must be finite and non-negative.")
    # Decimal keeps user-entered price steps exact when sizing whole shares.
    entry_decimal = Decimal(str(entry))
    risk_decimal = entry_decimal - Decimal(str(stop))
    reward_decimal = Decimal(str(target)) - entry_decimal
    cost_decimal = entry_decimal * Decimal(str(round_trip_cost_pct or 0)) / 100
    limits: list[int] = []
    if risk_budget is not None:
        limits.append(math.floor(Decimal(str(risk_budget)) / (risk_decimal + cost_decimal)))
    if cash_budget is not None:
        limits.append(math.floor(Decimal(str(cash_budget)) / (entry_decimal + cost_decimal)))
    quantity = min(limits) if limits else None
    result = TradeRisk(
        entry=entry, stop=stop, target=target,
        risk_per_share=float(risk_decimal), reward_per_share=float(reward_decimal),
        stop_distance_pct=float(risk_decimal / entry_decimal * 100),
        target_distance_pct=float(reward_decimal / entry_decimal * 100),
        reward_to_risk=float(reward_decimal / risk_decimal),
        round_trip_cost_pct=round_trip_cost_pct,
        net_reward_to_risk=(float((reward_decimal - cost_decimal) / (risk_decimal + cost_decimal))
                           if round_trip_cost_pct is not None else None),
        max_quantity=quantity,
        planned_risk_amount=float(quantity * (risk_decimal + cost_decimal)) if quantity is not None else None,
        notional=float(quantity * entry_decimal) if quantity is not None else None,
    )
    if any(value is not None and not math.isfinite(value) for value in (
        result.stop_distance_pct, result.target_distance_pct, result.reward_to_risk,
        result.net_reward_to_risk, result.planned_risk_amount, result.notional,
    )):
        raise ValueError("Plan exceeds the supported numeric range.")
    return result
