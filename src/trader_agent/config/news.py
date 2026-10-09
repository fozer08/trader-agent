from typing import ClassVar

from pydantic import BaseModel, Field, HttpUrl, model_validator

from .base import BaseConfig


class FeedConfig(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, pattern=r"^[a-z0-9_-]+$")
    url: HttpUrl


class NewsConfig(BaseConfig):
    CONFIG_FILENAME: ClassVar[str] = "news.yaml"

    feeds: list[FeedConfig] = Field(min_length=1)
    rss_timeout: float = Field(default=15, gt=0, allow_inf_nan=False)
    max_feed_bytes: int = Field(default=2_000_000, gt=0)
    fetch_concurrency: int = Field(default=4, gt=0)
    digest_concurrency: int = Field(default=3, gt=0)
    llm_timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    job_timeout: float = Field(default=300, gt=0, allow_inf_nan=False)
    max_input_chars: int = Field(default=60_000, ge=2000)
    max_output_tokens: int = Field(default=1024, gt=0)

    @model_validator(mode="after")
    def unique_feeds(self) -> "NewsConfig":
        if len({feed.name for feed in self.feeds}) != len(self.feeds):
            raise ValueError("RSS kaynak adları tekrarlanamaz.")
        if len({str(feed.url) for feed in self.feeds}) != len(self.feeds):
            raise ValueError("RSS kaynak adresleri tekrarlanamaz.")
        return self
