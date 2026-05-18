from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import pytest
import yaml

from trader_agent.config.market import MarketConfig


MARKET_YAML = {
    "watchlist": "watchlist.json",
    "exchange": {
        "name": "Borsa İstanbul",
        "code": "bist",
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


def test_exchange_loaded(market_config):
    assert market_config.exchange.code == "bist"


def test_exchange_name(market_config):
    assert market_config.exchange.name == "Borsa İstanbul"


def test_exchange_mic(market_config):
    assert market_config.exchange.mic == "XIST"


def test_exchange_tz(market_config):
    assert market_config.exchange.tz == ZoneInfo("Europe/Istanbul")


def test_session_times(market_config):
    assert market_config.exchange.open == time(10, 0)
    assert market_config.exchange.close == time(18, 0)


def test_trading_session(market_config):
    ts = market_config.exchange.trading_session()
    assert ts.start == time(10, 0)
    assert ts.end == time(18, 0)
    assert ts.timezone == ZoneInfo("Europe/Istanbul")


def test_watchlist_path(market_config, cfg_dir):
    assert market_config.watchlist_path() == cfg_dir / "data" / "watchlist.json"


def test_defaults_when_yaml_missing(cfg_dir):
    """market.yaml yoksa default ExchangeConfig (BIST) ile yüklenir."""
    config = MarketConfig.load()
    assert config.exchange.code == "bist"
    assert config.exchange.timezone == "Europe/Istanbul"


def test_unknown_field_rejected(cfg_dir):
    (cfg_dir / "market.yaml").write_text(yaml.dump({**MARKET_YAML, "unknown_field": True}))
    with pytest.raises(Exception):
        MarketConfig.load()
