from __future__ import annotations

from trader_agent.analysis import (
    BollingerResult,
    LiveSessionPulse,
    PriceLevels,
    compute_indicator,
    compute_levels,
    compute_pulse,
)
from trader_agent.analysis.indicators import BollingerResult as IndicatorsBollingerResult
from trader_agent.analysis.levels import PriceLevels as LevelsPriceLevels
from trader_agent.analysis.pulse import LiveSessionPulse as PulseLiveSessionPulse


def test_pulse_api_is_exported_from_analysis_package():
    assert LiveSessionPulse is PulseLiveSessionPulse
    assert callable(compute_pulse)


def test_levels_api_is_exported_from_analysis_package():
    assert PriceLevels is LevelsPriceLevels
    assert callable(compute_levels)


def test_indicator_api_is_exported_from_analysis_package():
    assert BollingerResult is IndicatorsBollingerResult
    assert callable(compute_indicator)
