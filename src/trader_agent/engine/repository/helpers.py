from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol cannot be empty.")
    return normalized


def validate_price(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number.")
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be finite and positive.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
