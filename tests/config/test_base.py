import pytest
from pydantic import ValidationError

from trader_agent.config.llm import LLMConfig
from trader_agent.config.market import MarketConfig
from trader_agent.config.news import NewsConfig


@pytest.mark.parametrize("config_type", [LLMConfig, MarketConfig, NewsConfig])
def test_missing_config_fails_without_creating_files(cfg_dir, config_type):
    with pytest.raises(FileNotFoundError):
        config_type.load()
    assert list(cfg_dir.iterdir()) == []


@pytest.mark.parametrize("config_type", [LLMConfig, MarketConfig, NewsConfig])
@pytest.mark.parametrize("content", ["", "null", "[]", "false", "0", "7", "string"])
def test_invalid_yaml_root_is_rejected_without_rewriting(cfg_dir, config_type, content):
    path = cfg_dir / config_type.CONFIG_FILENAME
    path.write_text(content)
    with pytest.raises(ValidationError):
        config_type.load()
    assert path.read_text() == content
