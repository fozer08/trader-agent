from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import pytest
import yaml

from trader_agent.config.market import MarketConfig


MARKET_YAML = {
    "watchlist": "watchlist.json",
    "exchanges": {
        "bist": {
            "name": "Borsa İstanbul",
            "mic": "XIST",
            "timezone": "Europe/Istanbul",
            "symbol_suffix": ":BIST",
            "markets": {
                "equities": {"session": {"open": "10:00", "close": "18:00"}},
                "derivatives": {"session": {"open": "09:30", "close": "18:15"}},
            },
        }
    },
}


@pytest.fixture
def market_config(cfg_dir):
    (cfg_dir / "market.yaml").write_text(yaml.dump(MARKET_YAML))
    return MarketConfig.load()


def test_exchanges_loaded(market_config):
    assert "bist" in market_config.exchanges


def test_exchange_name(market_config):
    assert market_config.exchanges["bist"].name == "Borsa İstanbul"


def test_exchange_mic(market_config):
    assert market_config.exchanges["bist"].mic == "XIST"


def test_exchange_tz(market_config):
    assert market_config.exchanges["bist"].tz == ZoneInfo("Europe/Istanbul")


def test_session_times(market_config):
    session = market_config.exchanges["bist"].markets["equities"].session
    assert session.open == time(10, 0)
    assert session.close == time(18, 0)


def test_derivatives_session(market_config):
    session = market_config.exchanges["bist"].markets["derivatives"].session
    assert session.open == time(9, 30)
    assert session.close == time(18, 15)


def test_trading_session(market_config):
    ts = market_config.exchanges["bist"].trading_session("equities")
    assert ts.start == time(10, 0)
    assert ts.end == time(18, 0)
    assert ts.timezone == ZoneInfo("Europe/Istanbul")


def test_watchlist_path(market_config, cfg_dir):
    assert market_config.watchlist_path() == cfg_dir / "data" / "watchlist.json"


def test_unknown_field_rejected(cfg_dir):
    (cfg_dir / "market.yaml").write_text(yaml.dump({**MARKET_YAML, "unknown_field": True}))
    with pytest.raises(Exception):
        MarketConfig.load()
