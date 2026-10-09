from __future__ import annotations

import logging
import os
from pathlib import Path


def setup_logging(logs_dir: Path | None = None) -> None:
    """Console ve isteğe bağlı file handler ile root logger'ı kurar.

    `logs_dir` caller tarafından çözülür —
    utils/ config'e bağlı değil, foundation katmanında kalır.
    """
    # LOG_LEVEL'ı çağrı sırasında oku — load_env() sonrası .env değerleri yansısın diye.
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logs_dir / "trader_agent.log", encoding="utf-8"))
    logging.basicConfig(
        level=log_level,
        format=fmt,
        handlers=handlers,
    )
    # Gürültülü 3rd-party logger'ları WARNING'e indir.
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
