from __future__ import annotations

from datetime import datetime, timezone


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol cannot be empty.")
    return normalized


def validate_price(value: float, field: str) -> None:
    if value <= 0:
        raise ValueError(f"{field} must be positive.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
