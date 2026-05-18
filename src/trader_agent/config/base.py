from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Self

import yaml
from pydantic import BaseModel

from ..env import require_path_env


def configs_dir() -> Path:
    return require_path_env("TRADER_CONFIGS_DIR")


def data_dir() -> Path:
    return require_path_env("TRADER_DATA_DIR")


def logs_dir() -> Path:
    return require_path_env("TRADER_LOGS_DIR")


class BaseConfig(BaseModel):
    CONFIG_FILENAME: ClassVar[str]
    model_config = {"extra": "forbid"}

    @classmethod
    def _config_path(cls) -> Path:
        return configs_dir() / cls.CONFIG_FILENAME

    @classmethod
    def default(cls) -> Self:
        return cls()

    @classmethod
    def load(cls) -> Self:
        path = cls._config_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls.model_validate(data)
        instance = cls.default()
        instance.save()
        return instance

    def save(self) -> None:
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self.model_dump(mode="json"),
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
            os.replace(tmp_path, path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def update(self, **kwargs: Any) -> None:
        fields = type(self).model_fields
        unknown_keys = set(kwargs) - set(fields)
        if unknown_keys:
            raise KeyError(f"Unknown config field(s): {', '.join(sorted(unknown_keys))}")
        current = {name: getattr(self, name) for name in fields}
        validated = type(self).model_validate({**current, **kwargs})
        for key in kwargs:
            object.__setattr__(self, key, getattr(validated, key))
        self.save()
