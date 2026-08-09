from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from f117.domain import FeedSource


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="F117_",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://f117:f117@postgres:5432/f117"
    rss_catalog_path: Path = Path("config/feeds.json")

    scheduler_interval_minutes: int = Field(default=180, ge=5)
    candidate_lookback_hours: int = Field(default=24, ge=1, le=168)
    dedup_lookback_days: int = Field(default=7, ge=1, le=30)
    dedup_title_threshold: float = Field(default=0.92, ge=0.8, le=1.0)
    digest_top_n: int = Field(default=10, ge=1, le=20)

    http_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    http_max_response_bytes: int = Field(default=5_000_000, ge=100_000)
    rss_max_concurrency: int = Field(default=8, ge=1, le=32)
    http_user_agent: str = "Radar-Intelligence-Engine/0.1 (+personal-feed-reader)"

    rank_freshness_weight: float = Field(default=20.0, ge=0.0)
    rank_reputation_weight: float = Field(default=15.0, ge=0.0)
    rank_mentions_weight: float = Field(default=15.0, ge=0.0)
    rank_popularity_weight: float = Field(default=15.0, ge=0.0)
    rank_growth_weight: float = Field(default=10.0, ge=0.0)
    rank_novelty_weight: float = Field(default=10.0, ge=0.0)
    rank_topic_fit_weight: float = Field(default=10.0, ge=0.0)
    rank_unusualness_weight: float = Field(default=5.0, ge=0.0)
    rank_freshness_half_life_hours: float = Field(default=36.0, gt=0.0)
    rank_full_mentions: int = Field(default=5, ge=2)

    openai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    openai_max_concurrency: int = Field(default=3, ge=1, le=10)
    openai_max_output_tokens: int = Field(default=500, ge=100, le=2000)

    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_format: Literal["editorial", "debug"] = "editorial"

    dry_run: bool = True
    log_level: str = "INFO"

    def load_feed_sources(self) -> list[FeedSource]:
        payload = json.loads(self.rss_catalog_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("RSS catalog must be a JSON array")
        return [FeedSource.model_validate(item) for item in payload]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
