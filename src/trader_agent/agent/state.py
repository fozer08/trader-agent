from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX_THESES = 10
_MAX_DECISIONS = 25


class ActiveThesis(BaseModel):
    """Bir hisse için aktif olarak izlenen veya geçmişte değerlendirilen tez."""

    model_config = ConfigDict(extra="forbid")

    stance: Literal["watching", "committed", "passed"]
    direction: Literal["long", "short"]
    thesis: str = Field(max_length=200)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    noted_at: datetime
    last_reviewed: datetime
    status_note: str = Field(default="", max_length=200)


class DecisionOutcome(BaseModel):
    """Bir AL/SAT öneri sonrası gözlenen sonuç."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["hit_target", "hit_stop", "open", "closed_manual"]
    exit_price: float | None = None
    note: str = Field(default="", max_length=200)


class Decision(BaseModel):
    """Agent'ın verdiği AL/SAT önerisinin kaydı."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    type: Literal["BUY", "SELL"]
    entry: float
    stop: float
    target: float | None = None
    rationale: str = Field(max_length=300)
    at: datetime
    outcome: DecisionOutcome | None = None


class MarketView(BaseModel):
    """Genel piyasa görüşü ve gündemdeki temalar."""

    model_config = ConfigDict(extra="forbid")

    stance: str = Field(max_length=500)
    themes: list[str] = Field(default_factory=list, max_length=5)
    updated_at: datetime


class UserContext(BaseModel):
    """Kullanıcıdan öğrenilen tercih ve kısıtlar."""

    model_config = ConfigDict(extra="forbid")

    preferences: list[str] = Field(default_factory=list, max_length=10)
    constraints: list[str] = Field(default_factory=list, max_length=10)
    open_questions: list[str] = Field(default_factory=list, max_length=5)


class AgentState(BaseModel):
    """Agent'ın turlar arası hafızası; trim sonrası kaybolmayacak yorumsal kayıt."""

    model_config = ConfigDict(extra="forbid")

    active_theses: dict[str, ActiveThesis] = Field(default_factory=dict)
    decisions: list[Decision] = Field(default_factory=list, max_length=_MAX_DECISIONS)
    market_view: MarketView | None = None
    user_context: UserContext = Field(default_factory=UserContext)

    @model_validator(mode="after")
    def _limit_theses(self) -> "AgentState":
        if len(self.active_theses) > _MAX_THESES:
            raise ValueError(f"active_theses en fazla {_MAX_THESES} sembol içerebilir.")
        return self

    def is_empty(self) -> bool:
        return (
            not self.active_theses
            and not self.decisions
            and self.market_view is None
            and not self.user_context.preferences
            and not self.user_context.constraints
            and not self.user_context.open_questions
        )
