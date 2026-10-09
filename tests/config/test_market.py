from __future__ import annotations

from datetime import time

import pytest
import yaml

from trader_agent.config.market import ExchangeConfig, MarketConfig


MARKET_YAML = {
    "stocklist": "stocklist.json",
    "calendar": "calendar.json",
    "exchange": {
        "name": "Borsa İstanbul",
        "mic": "XIST",
        "timezone": "Europe/Istanbul",
        "open": "10:00",
        "close": "18:00",
    },
}


@pytest.fixture
def market_config(cfg_dir):
    (cfg_dir / "market.yaml").write_text(yaml.dump(MARKET_YAML))
    return MarketConfig.load()


def test_exchange_name(market_config):
    assert market_config.exchange.name == "Borsa İstanbul"


def test_exchange_mic(market_config):
    assert market_config.exchange.mic == "XIST"


def test_exchange_timezone(market_config):
    assert market_config.exchange.timezone == "Europe/Istanbul"


def test_session_times(market_config):
    assert market_config.exchange.open == time(10, 0)
    assert market_config.exchange.close == time(18, 0)


def test_stocklist_path(market_config, cfg_dir):
    assert market_config.stocklist_path() == cfg_dir / "data" / "stocklist.json"


def test_defaults():
    config = MarketConfig()
    assert config.exchange.mic == "XIST"
    assert config.exchange.timezone == "Europe/Istanbul"


def test_unknown_field_rejected(cfg_dir):
    (cfg_dir / "market.yaml").write_text(yaml.dump({**MARKET_YAML, "unknown_field": True}))
    with pytest.raises(Exception):
        MarketConfig.load()


@pytest.mark.parametrize(
    "changes",
    [
        {"weekdays": []},
        {"weekdays": ["mon", "mon"]},
        {"weekdays": ["monday"]},
        {"timezone": "invalid/zone"},
        {"open": "18:00"},
        {"close": "09:00"},
        {"daily_close": "17:59"},
        {"open": "10:00+03:00"},
    ],
)
def test_invalid_exchange_schedule_rejected(changes):
    with pytest.raises(ValueError):
        ExchangeConfig(**changes)


def test_seconds_are_preserved_in_settings():
    exchange = ExchangeConfig(open="10:00:30", close="18:00:30")
    assert exchange.open.second == 30
    assert exchange.close.second == 30
