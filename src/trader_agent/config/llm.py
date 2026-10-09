from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from .base import BaseConfig


class LLMConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "llm.yaml"

    agent_model: str = "claude-sonnet-5-5"   # Ana konuşma (Phase 1)
    light_model: str = "claude-haiku-5-5"   # State generation, özetleme gibi yardımcı işler (Phase 2)
    max_output_tokens: int = Field(default=8192, gt=0)
    agent_effort: Literal["low", "medium", "high"] = "medium"
    turn_timeout: float = Field(default=180, gt=0, allow_inf_nan=False)
