from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from trader_agent.config.llm import LLMConfig


def test_defaults(cfg_dir):
    (cfg_dir / "llm.yaml").write_text("{}\n")
    config = LLMConfig.load()
    assert config.agent_model == "claude-sonnet-5-5"
    assert config.light_model == "claude-haiku-5-5"
    assert config.max_output_tokens == 8192
    assert config.agent_effort == "medium"
    assert config.turn_timeout == 180


def test_yaml_overrides_defaults(cfg_dir):
    path = cfg_dir / "llm.yaml"
    content = yaml.dump({"max_output_tokens": 2048, "agent_effort": "high"})
    path.write_text(content)
    config = LLMConfig.load()
    assert config.max_output_tokens == 2048
    assert config.agent_effort == "high"
    assert path.read_text() == content


@pytest.mark.parametrize("field", ["api_key", "provider", "unknown_field"])
def test_unknown_field_rejected(cfg_dir, field):
    (cfg_dir / "llm.yaml").write_text(yaml.dump({field: "test-value"}))
    with pytest.raises(ValidationError):
        LLMConfig.load()


@pytest.mark.parametrize("max_output_tokens", [0, -1])
def test_invalid_token_limit_rejected(cfg_dir, max_output_tokens):
    (cfg_dir / "llm.yaml").write_text(yaml.dump({"max_output_tokens": max_output_tokens}))
    with pytest.raises(ValidationError):
        LLMConfig.load()


@pytest.mark.parametrize("effort", ["max", "turbo", "", 5])
def test_invalid_effort_rejected(effort):
    with pytest.raises(ValidationError):
        LLMConfig(agent_effort=effort)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_turn_timeout_rejected(timeout):
    with pytest.raises(ValidationError):
        LLMConfig(turn_timeout=timeout)
