from __future__ import annotations

import os
from typing import ClassVar, Literal

from pydantic import Field

from .base import BaseConfig

DEFAULT_AGENT_MODELS = {
    "anthropic": "claude-opus-4-7",
}

DEFAULT_LIGHT_MODELS = {
    "anthropic": "claude-haiku-4-5",
}


class LLMConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "llm.yaml"

    provider: Literal["anthropic"] = "anthropic"
    api_key: str = Field(default="", exclude=True)
    agent_model: str = ""   # Ana konuşma (Phase 1)
    light_model: str = ""   # State generation, özetleme gibi yardımcı işler (Phase 2)
    max_output_tokens: int = 8192
    use_extended_thinking: bool = False

    def model_post_init(self, __context):
        if not self.api_key:
            object.__setattr__(self, "api_key", os.environ.get("ANTHROPIC_API_KEY", ""))
        if not self.agent_model:
            object.__setattr__(self, "agent_model", DEFAULT_AGENT_MODELS[self.provider])
        if not self.light_model:
            object.__setattr__(self, "light_model", DEFAULT_LIGHT_MODELS[self.provider])
