import os
from typing import ClassVar, Literal

from pydantic import Field

from .base import BaseConfig


DEFAULT_LIGHT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
}

DEFAULT_HEAVY_MODELS = {
    "anthropic": "claude-opus-4-7",
}


class LLMConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "llm.yaml"

    provider: Literal["anthropic"] = "anthropic"
    api_key: str = Field(default="", exclude=True)
    light_model: str = ""
    heavy_model: str = ""
    max_tokens: int = 4096
    use_prompt_caching: bool = True
    use_extended_thinking: bool = True
    streaming: bool = True
    max_conversation_history: int = 20

    def model_post_init(self, __context):
        if not self.api_key:
            object.__setattr__(self, "api_key", os.environ.get("ANTHROPIC_API_KEY", ""))
        if not self.light_model:
            object.__setattr__(self, "light_model", DEFAULT_LIGHT_MODELS[self.provider])
        if not self.heavy_model:
            object.__setattr__(self, "heavy_model", DEFAULT_HEAVY_MODELS[self.provider])
