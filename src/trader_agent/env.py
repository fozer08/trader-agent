from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env() -> None:
    load_dotenv()


def require_path_env(name: str) -> Path:
    load_env()
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} zorunlu. Bu dizini .env veya ortam degiskeni ile tanimlayin."
        )
    return Path(value).expanduser()


def optional_path_env(name: str, default: Path) -> Path:
    load_env()
    value = os.environ.get(name)
    if not value:
        return default
    return Path(value).expanduser()
