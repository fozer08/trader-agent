import pytest


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_CONFIGS_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRADER_LOGS_DIR", str(tmp_path / "logs"))
    return tmp_path


@pytest.fixture
def bist_calendar(monkeypatch):
    from pathlib import Path
    from datetime import time
    from trader_agent.config.market import ExchangeConfig, MarketConfig
    from trader_agent.engine.calendar.loader import load_calendar

    monkeypatch.setenv("TRADER_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))
    exchange = ExchangeConfig(daily_close=time(18, 10))
    return load_calendar(MarketConfig(exchange=exchange))
