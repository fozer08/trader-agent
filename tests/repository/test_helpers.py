from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trader_agent.repository._helpers import normalize_symbol, utc_now, validate_price


# ---- normalize_symbol --------------------------------------------------------

def test_normalize_symbol_uppercases():
    assert normalize_symbol("thyao") == "THYAO"


def test_normalize_symbol_strips_whitespace():
    assert normalize_symbol("  thyao  ") == "THYAO"


def test_normalize_symbol_handles_mixed_case_and_padding():
    assert normalize_symbol(" ThyAo ") == "THYAO"


def test_normalize_symbol_already_normalized_is_idempotent():
    assert normalize_symbol("THYAO") == "THYAO"


def test_normalize_symbol_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        normalize_symbol("")


def test_normalize_symbol_whitespace_only_raises():
    with pytest.raises(ValueError, match="empty"):
        normalize_symbol("   ")


# ---- validate_price ----------------------------------------------------------

def test_validate_price_positive_ok():
    validate_price(0.01, "entry")
    validate_price(100.0, "entry")
    validate_price(1_000_000.0, "entry")


def test_validate_price_zero_raises():
    with pytest.raises(ValueError, match="entry"):
        validate_price(0.0, "entry")


def test_validate_price_negative_raises():
    with pytest.raises(ValueError, match="stop"):
        validate_price(-1.0, "stop")


def test_validate_price_includes_field_name_in_message():
    with pytest.raises(ValueError, match="target"):
        validate_price(-5.0, "target")


# ---- utc_now -----------------------------------------------------------------

def test_utc_now_returns_aware_datetime():
    dt = utc_now()
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None


def test_utc_now_tz_is_utc():
    dt = utc_now()
    assert dt.utcoffset().total_seconds() == 0


def test_utc_now_is_recent():
    """utc_now sürekli artmalı; iki ardışık çağrı arası 1 saniyeden az olur."""
    a = utc_now()
    b = utc_now()
    assert b >= a
    assert (b - a).total_seconds() < 1.0


def test_utc_now_tz_is_timezone_utc_singleton():
    assert utc_now().tzinfo == timezone.utc
