import pytest
from pydantic import ValidationError

from trader_agent.config.news import NewsConfig

FEED = {"name": "test", "url": "https://example.test/rss"}


@pytest.mark.parametrize("overrides", [
    {"feeds": []}, {"feeds": [FEED, FEED]}, {"feeds": [{"name": "bad name", "url": "invalid"}]},
    {"rss_timeout": 0}, {"job_timeout": float("inf")}, {"digest_concurrency": 0},
    {"fetch_concurrency": -1}, {"max_feed_bytes": 0},
    {"max_input_chars": 100}, {"max_output_tokens": 0}, {"unknown": True},
])
def test_invalid_settings_fail_at_boundary(overrides):
    with pytest.raises(ValidationError):
        NewsConfig.model_validate({"feeds": [FEED]} | overrides)


def test_load_news_config(cfg_dir):
    (cfg_dir / "news.yaml").write_text("feeds:\n  - name: test\n    url: https://example.test/rss\n")
    config = NewsConfig.load()
    assert config.feeds[0].name == "test"
    assert config.digest_concurrency == 3
