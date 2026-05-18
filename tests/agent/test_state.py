from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trader_agent.agent.state import (
    ActiveThesis,
    AgentState,
    Decision,
    DecisionOutcome,
    UserContext,
)


# ---- Helpers -----------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)


def _thesis(stance: str = "watching", thesis: str = "EMA21 üstü tutunma") -> ActiveThesis:
    return ActiveThesis(
        stance=stance,
        thesis=thesis,
        noted_at=_now(),
        last_reviewed=_now(),
    )


def _decision(symbol: str = "THYAO", type_: str = "BUY") -> Decision:
    return Decision(
        symbol=symbol,
        type=type_,
        entry=95.5,
        stop=92.0,
        target=102.0,
        rationale="Conviction: yüksek — EMA21 onaylı",
        at=_now(),
    )


# ---- AgentState.is_empty -----------------------------------------------------

def test_is_empty_for_default_state():
    assert AgentState().is_empty() is True


def test_is_empty_false_when_thesis_added():
    state = AgentState(active_theses={"THYAO": _thesis()})
    assert state.is_empty() is False


def test_is_empty_false_when_decision_added():
    state = AgentState(decisions=[_decision()])
    assert state.is_empty() is False


def test_is_empty_false_when_user_preference_added():
    state = AgentState(user_context=UserContext(preferences=["ATR bazlı stop"]))
    assert state.is_empty() is False


def test_is_empty_false_when_user_constraint_added():
    state = AgentState(user_context=UserContext(constraints=["overnight pozisyon almam"]))
    assert state.is_empty() is False


# ---- _limit_theses validator -------------------------------------------------

def test_active_theses_accepts_up_to_10():
    """Sınır kapaklı: 10 sembol kabul edilir."""
    theses = {f"SYM{i}": _thesis() for i in range(10)}
    state = AgentState(active_theses=theses)
    assert len(state.active_theses) == 10


def test_active_theses_rejects_11():
    """11. sembol model_validator ile ValueError fırlatır."""
    theses = {f"SYM{i}": _thesis() for i in range(11)}
    with pytest.raises(ValidationError, match="10 sembol"):
        AgentState(active_theses=theses)


# ---- decisions max_length ----------------------------------------------------

def test_decisions_accepts_up_to_25():
    decisions = [_decision() for _ in range(25)]
    state = AgentState(decisions=decisions)
    assert len(state.decisions) == 25


def test_decisions_rejects_26():
    decisions = [_decision() for _ in range(26)]
    with pytest.raises(ValidationError):
        AgentState(decisions=decisions)


# ---- Decision.type literal ---------------------------------------------------

def test_decision_type_accepts_buy_and_sell():
    _decision(type_="BUY")
    _decision(type_="SELL")


def test_decision_type_rejects_turkish_labels():
    """Agent çıktısı 'AL'/'SAT'; state-gen bunları BUY/SELL'e eşlemek zorunda."""
    with pytest.raises(ValidationError):
        _decision(type_="AL")
    with pytest.raises(ValidationError):
        _decision(type_="SAT")


def test_decision_type_rejects_lowercase():
    with pytest.raises(ValidationError):
        _decision(type_="buy")


# ---- Decision.rationale length -----------------------------------------------

def test_decision_rationale_accepts_400_chars():
    Decision(
        symbol="THYAO",
        type="BUY",
        entry=100.0,
        stop=95.0,
        target=110.0,
        rationale="x" * 400,
        at=_now(),
    )


def test_decision_rationale_rejects_401_chars():
    with pytest.raises(ValidationError):
        Decision(
            symbol="THYAO",
            type="BUY",
            entry=100.0,
            stop=95.0,
            target=110.0,
            rationale="x" * 401,
            at=_now(),
        )


# ---- Decision.target nullable ------------------------------------------------

def test_decision_target_is_optional():
    d = Decision(
        symbol="THYAO",
        type="BUY",
        entry=100.0,
        stop=95.0,
        rationale="tetik onaylı",
        at=_now(),
    )
    assert d.target is None


# ---- DecisionOutcome literal -------------------------------------------------

@pytest.mark.parametrize("status", ["hit_target", "hit_stop", "open", "closed_manual"])
def test_decision_outcome_accepts_known_statuses(status):
    DecisionOutcome(status=status)


def test_decision_outcome_rejects_unknown_status():
    with pytest.raises(ValidationError):
        DecisionOutcome(status="cancelled")


# ---- ActiveThesis.stance literal ---------------------------------------------

@pytest.mark.parametrize("stance", ["watching", "committed", "passed"])
def test_active_thesis_accepts_known_stances(stance):
    _thesis(stance=stance)


def test_active_thesis_rejects_unknown_stance():
    with pytest.raises(ValidationError):
        _thesis(stance="planning")


# ---- ActiveThesis: no price fields (anti-stale-data) -------------------------

def test_active_thesis_rejects_entry_field():
    """Schema kasıtlı olarak entry/stop/target tutmaz — canlı veriyi baskılamasın."""
    with pytest.raises(ValidationError):
        ActiveThesis(
            stance="watching",
            thesis="x",
            noted_at=_now(),
            last_reviewed=_now(),
            entry=95.0,  # extra="forbid" — bu field yok
        )


def test_active_thesis_rejects_stop_field():
    with pytest.raises(ValidationError):
        ActiveThesis(
            stance="watching",
            thesis="x",
            noted_at=_now(),
            last_reviewed=_now(),
            stop=92.0,
        )


# ---- extra="forbid" on all models -------------------------------------------

def test_agent_state_rejects_unknown_top_level_field():
    """Eski market_view alanı çıkarıldı — varsa hata."""
    with pytest.raises(ValidationError):
        AgentState(market_view={"stance": "bullish"})


def test_user_context_rejects_open_questions_field():
    """open_questions çıkarıldı (summary'ye taşındı)."""
    with pytest.raises(ValidationError):
        UserContext(open_questions=["X event etkiler mi?"])


# ---- UserContext list caps ---------------------------------------------------

def test_user_context_preferences_max_10():
    UserContext(preferences=[f"pref-{i}" for i in range(10)])
    with pytest.raises(ValidationError):
        UserContext(preferences=[f"pref-{i}" for i in range(11)])


def test_user_context_constraints_max_10():
    UserContext(constraints=[f"c-{i}" for i in range(10)])
    with pytest.raises(ValidationError):
        UserContext(constraints=[f"c-{i}" for i in range(11)])


# ---- Roundtrip serialization -------------------------------------------------

def test_state_roundtrip_via_json():
    """model_dump_json + model_validate_json bütünlüğü."""
    state = AgentState(
        active_theses={"THYAO": _thesis()},
        decisions=[_decision()],
        user_context=UserContext(preferences=["ATR stop"]),
    )
    payload = state.model_dump_json()
    restored = AgentState.model_validate_json(payload)
    assert restored.active_theses["THYAO"].thesis == "EMA21 üstü tutunma"
    assert restored.decisions[0].symbol == "THYAO"
    assert restored.user_context.preferences == ["ATR stop"]
