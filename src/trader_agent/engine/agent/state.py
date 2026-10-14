from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..analysis import Horizon, RiskLevel

_MAX_THESES = 10
_MAX_DECISIONS = 25


class ActiveThesis(BaseModel):
    """Bir hisse için aktif olarak izlenen qualitative tez.

    Somut fiyat seviyeleri (entry/stop/target) BURADA tutulmaz; her seferinde
    ``analyze`` ile yeniden alınır. State'in
    plan numaralarını cache'leyip canlı veriyi baskılamasını önler. Tez sadece
    niyet, tetik tipi ve durum notu olarak kayda alınır.
    """

    model_config = ConfigDict(extra="forbid")

    horizon: Horizon | None = None
    stance: Literal["watching", "committed", "passed"]
    thesis: str = Field(max_length=200)
    noted_at: datetime
    last_reviewed: datetime
    status_note: str = Field(default="", max_length=200)


class DecisionOutcome(BaseModel):
    """Bir AL/SAT öneri sonrası gözlenen sonuç."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["hit_target", "hit_stop", "open", "closed_manual"]
    exit_price: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    note: str = Field(default="", max_length=200)


class Decision(BaseModel):
    """Agent'ın verdiği AL/SAT önerisinin kaydı."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    type: Literal["BUY", "SELL"]
    entry: float = Field(gt=0, allow_inf_nan=False, strict=True)
    stop: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    target: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    horizon: Horizon | None = None
    risk_level: RiskLevel | None = None
    rationale: str = Field(max_length=400)
    at: datetime
    outcome: DecisionOutcome | None = None

    @model_validator(mode="after")
    def require_buy_stop(self) -> Self:
        """BUY kaydı tam bir long planına dayanır; SELL için yeni stop gerekmez."""
        if self.type == "BUY" and self.stop is None:
            raise ValueError("A BUY decision must include its planned stop.")
        return self


class UserContext(BaseModel):
    """Bu sohbetin güncel tercih ve kısıtlarını turlar arasında taşır.

    Arayüz seçimleri uygulama tarafından korunur; geçmiş tercihler mesajların
    tarihli bağlamındadır. Sohbet sıfırlanınca bu kayıt da temizlenir.
    """

    model_config = ConfigDict(extra="forbid")

    horizon: Horizon | None = None
    risk_level: RiskLevel | None = None
    preferences: list[str] = Field(default_factory=list, max_length=10)
    constraints: list[str] = Field(default_factory=list, max_length=10)


class AgentState(BaseModel):
    """Agent'ın turlar arası hafızası; trim sonrası kaybolmayacak yorumsal kayıt.

    Genel piyasa görüşü gibi sistemin erişimi olmayan (makro, endeks, FX, sektör)
    verilere dayanan alanlar burada **tutulmaz**; her seferinde shallow
    derivation'a düşer. Piyasa hissi yorumu varsa summary'e gömülür.
    """

    model_config = ConfigDict(extra="forbid")

    active_theses: dict[str, ActiveThesis] = Field(default_factory=dict, max_length=_MAX_THESES)
    decisions: list[Decision] = Field(default_factory=list, max_length=_MAX_DECISIONS)
    user_context: UserContext = Field(default_factory=UserContext)

    def is_empty(self) -> bool:
        return (
            not self.active_theses
            and not self.decisions
            and self.user_context.horizon is None
            and self.user_context.risk_level is None
            and not self.user_context.preferences
            and not self.user_context.constraints
        )
