from __future__ import annotations

from trader_agent.analysis import (
    BollingerResult,
    SessionSnapshot,
    compute_session_snapshot,
)
from trader_agent.analysis.levels import SessionSnapshot as LevelsSessionSnapshot
from trader_agent.analysis.technical import BollingerResult as TechnicalBollingerResult


def test_snapshot_api_is_exported_from_analysis_package():
    assert SessionSnapshot is LevelsSessionSnapshot
    assert callable(compute_session_snapshot)


def test_bollinger_result_is_exported_from_analysis_package():
    assert BollingerResult is TechnicalBollingerResult
