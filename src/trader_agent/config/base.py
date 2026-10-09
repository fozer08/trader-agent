from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Self

import yaml
from pydantic import BaseModel

from ..env import require_path_env


def configs_dir() -> Path:
    return require_path_env("TRADER_CONFIGS_DIR")


def data_dir() -> Path:
    return require_path_env("TRADER_DATA_DIR")


class BaseConfig(BaseModel):
    CONFIG_FILENAME: ClassVar[str]
    model_config = {"extra": "forbid"}

    @classmethod
    def load(cls) -> Self:
        with (configs_dir() / cls.CONFIG_FILENAME).open(encoding="utf-8") as file:
            return cls.model_validate(yaml.safe_load(file))
