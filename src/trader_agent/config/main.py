from __future__ import annotations

from dataclasses import dataclass

from .llm import LLMConfig
from .market import MarketConfig


@dataclass
class MainConfig:
    llm: LLMConfig
    market: MarketConfig

    @classmethod
    def load(cls) -> "MainConfig":
        return cls(
            llm=LLMConfig.load(),
            market=MarketConfig.load(),
        )
