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

    collection_interval_minutes: int = Field(default=180, ge=5)
    # Backwards-compatible override for deployments that still set the M1 name.
    scheduler_interval_minutes: int | None = Field(default=None, ge=5)
    candidate_lookback_hours: int = Field(default=24, ge=1, le=168)
    freshness_daily_max_age_days: int = Field(default=3, ge=1, le=365)
    freshness_youtube_daily_max_age_days: int = Field(default=3, ge=1, le=365)
    freshness_funny_wtf_max_age_days: int = Field(default=7, ge=1, le=365)
    freshness_discovery_max_age_days: int = Field(default=7, ge=1, le=365)
    dedup_lookback_days: int = Field(default=7, ge=1, le=30)
    dedup_title_threshold: float = Field(default=0.92, ge=0.8, le=1.0)
    digest_max_items: int = Field(default=5, ge=1, le=20)
    # Backwards-compatible override used by old configuration and callers.
    digest_top_n: int | None = Field(default=None, ge=1, le=20)
    digest_times: str = "09:00,15:00,21:00"
    delivery_timezone: str = "Europe/Moscow"
    minimum_delivery_score: float = Field(default=55.0, ge=0.0, le=100.0)
    minimum_editorial_fit: float = Field(default=55.0, ge=0.0, le=100.0)
    editorial_fit_weight: float = Field(default=0.65, ge=0.5, le=0.9)
    urgent_delivery_enabled: bool = True
    urgent_max_items: int = Field(default=2, ge=1, le=5)
    urgent_min_delivery_score: float = Field(default=76.0, ge=0.0, le=100.0)
    urgent_min_editorial_fit: float = Field(default=92.0, ge=0.0, le=100.0)

    github_delivery_min_stars: int = Field(default=1000, ge=0)
    github_delivery_min_star_velocity: float = Field(default=25.0, ge=0.0)
    github_delivery_min_forks: int = Field(default=100, ge=0)
    github_delivery_min_mentions: int = Field(default=2, ge=1)
    github_exceptional_editorial_fit: float = Field(default=88.0, ge=0.0, le=100.0)
    arxiv_min_editorial_fit: float = Field(default=72.0, ge=0.0, le=100.0)
    youtube_delivery_min_views: int = Field(default=50_000, ge=0)
    youtube_delivery_min_view_velocity: float = Field(default=2_000.0, ge=0.0)
    youtube_delivery_min_likes: int = Field(default=1_000, ge=0)
    youtube_delivery_min_mentions: int = Field(default=2, ge=1)
    reddit_delivery_min_upvotes: int = Field(default=250, ge=0)
    reddit_delivery_min_comments: int = Field(default=40, ge=0)
    reddit_delivery_min_velocity: float = Field(default=20.0, ge=0.0)
    reddit_delivery_min_mentions: int = Field(default=2, ge=1)
    reddit_min_editorial_fit: float = Field(default=75.0, ge=0.0, le=100.0)
    # RSS listings have no provider engagement metrics.  They need an explicit,
    # stricter event path instead of being treated as a zero-score API post.
    reddit_rss_min_editorial_fit: float = Field(default=80.0, ge=0.0, le=100.0)
    reddit_rss_exceptional_editorial_fit: float = Field(default=85.0, ge=0.0, le=100.0)

    http_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    http_max_response_bytes: int = Field(default=5_000_000, ge=100_000)
    rss_max_concurrency: int = Field(default=8, ge=1, le=32)
    http_user_agent: str = "Radar-Intelligence-Engine/0.1 (+personal-feed-reader)"
    metadata_fetch_max_response_bytes: int = Field(default=300_000, ge=10_000, le=2_000_000)
    metadata_fetch_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    github_api_token: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    youtube_api_key: SecretStr | None = None

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

    discovery_growth_weight: float = Field(default=35.0, ge=0.0)
    discovery_acceleration_weight: float = Field(default=15.0, ge=0.0)
    discovery_diversity_weight: float = Field(default=20.0, ge=0.0)
    discovery_novelty_weight: float = Field(default=15.0, ge=0.0)
    discovery_freshness_weight: float = Field(default=15.0, ge=0.0)
    discovery_min_baseline: float = Field(default=25.0, ge=1.0)
    discovery_min_growth_absolute: float = Field(default=10.0, ge=0.0)
    discovery_rising_threshold: float = Field(default=55.0, ge=0.0, le=100.0)
    discovery_hidden_gem_max_popularity: float = Field(default=2000.0, ge=1.0)
    discovery_selection_boost: float = Field(default=0.2, ge=0.0, le=0.5)

    diversity_max_per_source: int = Field(default=2, ge=1, le=10)
    diversity_max_per_entity: int = Field(default=2, ge=1, le=10)
    diversity_max_per_category: int = Field(default=4, ge=1, le=20)
    diversity_strong_score_threshold: float = Field(default=85.0, ge=0.0, le=100.0)

    openai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    openai_max_concurrency: int = Field(default=3, ge=1, le=10)
    openai_max_output_tokens: int = Field(default=500, ge=100, le=2000)
    editorial_max_attempts: int = Field(default=3, ge=1, le=10)
    editorial_retry_base_seconds: int = Field(default=60, ge=1, le=3600)
    editorial_retry_max_seconds: int = Field(default=3600, ge=1, le=86400)
    editorial_retry_slots: int = Field(default=2, ge=0, le=20)

    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_format: Literal["editorial", "debug"] = "editorial"
    telegram_feedback_enabled: bool = True
    telegram_feedback_poll_seconds: int = Field(default=10, ge=2, le=60)
    telegram_pace_seconds: float = Field(default=0.25, ge=0.0, le=5.0)
    delivery_claim_lease_seconds: int = Field(default=300, ge=30, le=86400)

    dry_run: bool = True
    log_level: str = "INFO"

    def load_feed_sources(self) -> list[FeedSource]:
        payload = json.loads(self.rss_catalog_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("RSS catalog must be a JSON array")
        return [FeedSource.model_validate(item) for item in payload]

    @property
    def effective_collection_interval_minutes(self) -> int:
        return self.scheduler_interval_minutes or self.collection_interval_minutes

    @property
    def effective_digest_max_items(self) -> int:
        return self.digest_top_n or self.digest_max_items


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
