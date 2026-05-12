from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .base import data_dir
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

    def database_path(self) -> Path:
        return data_dir() / "trader.db"
