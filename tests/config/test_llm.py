from __future__ import annotations

import yaml
import pytest

from trader_agent.config.llm import LLMConfig


def test_defaults(cfg_dir):
    config = LLMConfig.load()
    assert config.provider == "anthropic"
    assert config.light_model == "claude-sonnet-4-6"
    assert config.heavy_model == "claude-opus-4-7"
    assert config.max_tokens == 4096
    assert config.use_prompt_caching is True
    assert config.use_extended_thinking is True
    assert config.streaming is True


def test_yaml_overrides_defaults(cfg_dir):
    (cfg_dir / "llm.yaml").write_text(yaml.dump({"max_tokens": 2048}))
    config = LLMConfig.load()
    assert config.max_tokens == 2048


def test_update_saves_and_reloads(cfg_dir):
    config = LLMConfig.load()
    config.update(max_tokens=2048)
    assert LLMConfig.load().max_tokens == 2048


def test_api_key_from_env(cfg_dir, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert LLMConfig.load().api_key == "test-key"


def test_api_key_not_persisted(cfg_dir, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    LLMConfig.load()
    saved = yaml.safe_load((cfg_dir / "llm.yaml").read_text())
    assert "api_key" not in saved


def test_unknown_field_rejected(cfg_dir):
    (cfg_dir / "llm.yaml").write_text(yaml.dump({"unknown_field": True}))
    with pytest.raises(Exception):
        LLMConfig.load()
