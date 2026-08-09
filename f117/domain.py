from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def _default_reddit_rss_listings() -> list[Literal["new", "hot", "rising"]]:
    return ["new", "hot", "rising"]


class Category(StrEnum):
    AI = "ai"
    LLM = "llm"
    ROBOTICS = "robotics"
    RESEARCH = "research"
    OPEN_SOURCE = "open_source"
    HARDWARE = "hardware"
    BRAIN_INTERFACE = "brain_interface"
    CYBERCULTURE = "cyberculture"
    FUNNY = "funny"
    WTF = "wtf"
    OTHER = "other"


class FeedbackType(StrEnum):
    USEFUL = "useful"
    MISS = "miss"
    POST = "post"


class FeedSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    feed_url: HttpUrl
    site_url: HttpUrl | None = None
    reputation: float = Field(default=0.5, ge=0.0, le=1.0)
    enabled: bool = True
    default_categories: list[Category] = Field(default_factory=list)
    kind: Literal["rss", "hacker_news", "arxiv", "github", "reddit", "youtube"] = "rss"
    collection: Literal["top", "new", "best"] = "top"
    item_limit: int = Field(default=30, ge=1, le=100)
    arxiv_categories: list[str] = Field(default_factory=list)
    github_queries: list[str] = Field(default_factory=list)
    github_include_releases: bool = True
    reddit_subreddit: str | None = None
    reddit_listing: Literal["hot", "new", "top"] = "hot"
    # OAuth continues to use ``reddit_listing``.  These are only queried by the
    # public Atom fallback and are deliberately qualitative, not engagement data.
    reddit_rss_listings: list[Literal["new", "hot", "rising"]] = Field(
        default_factory=_default_reddit_rss_listings
    )
    youtube_channel_ids: list[str] = Field(default_factory=list)
    youtube_queries: list[str] = Field(default_factory=list)


class CollectedItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str
    source_key: str
    source_name: str
    source_reputation: float = Field(ge=0.0, le=1.0)
    title: str
    url: str
    published_at: datetime | None = None
    collected_at: datetime
    description: str = ""
    author: str | None = None
    source_categories: list[Category] = Field(default_factory=list)
    popularity: dict[str, float] = Field(default_factory=dict)
    qualitative_signals: list[str] = Field(default_factory=list)
    subreddit: str | None = None
    media_type: Literal["image", "video", "none"] = "none"
    media_url: str | None = None
    thumbnail_url: str | None = None
    media_source: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class NormalizedItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str
    source_key: str
    source_name: str
    source_reputation: float = Field(ge=0.0, le=1.0)
    title: str
    url: str
    canonical_url: str
    published_at: datetime
    collected_at: datetime
    description: str = ""
    author: str | None = None
    source_categories: list[Category] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=list)
    popularity: dict[str, float] = Field(default_factory=dict)
    qualitative_signals: list[str] = Field(default_factory=list)
    subreddit: str | None = None
    media_type: Literal["image", "video", "none"] = "none"
    media_url: str | None = None
    thumbnail_url: str | None = None
    media_source: str | None = None
    content_hash: str
    normalized_title: str


class EditorialEnrichment(BaseModel):
    model_config = ConfigDict(frozen=True)

    title_ru: str = Field(min_length=1, max_length=300)
    summary_ru: str = Field(min_length=1, max_length=1200)
    why_important: str = Field(min_length=1, max_length=700)
    audience_interest_reason: str = Field(
        default="Материал соответствует редакционному профилю Radar.",
        min_length=1,
        max_length=500,
    )
    ironic_comment: str = Field(default="id:28: наблюдение записано.", min_length=1, max_length=220)
    post_fit_score: int = Field(ge=0, le=10)


class StoredMaterial(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    item: NormalizedItem
    duplicate_of_id: UUID | None = None
    independent_mentions: int = Field(default=1, ge=1)
    score: float = 0.0
    score_reasons: list[str] = Field(default_factory=list)
    discovery_score: float = 0.0
    delivered_at: datetime | None = None
    llm_enrichment: EditorialEnrichment | None = None
    llm_model: str | None = None
    llm_usage: dict[str, int] = Field(default_factory=dict)
    editorial_attempts: int = Field(default=0, ge=0)
    editorial_retry_at: datetime | None = None
    delivery_started_at: datetime | None = None
    delivery_ambiguous_at: datetime | None = None


class MetricSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    captured_at: datetime
    metrics: dict[str, float]


class RankedMaterial(BaseModel):
    model_config = ConfigDict(frozen=True)

    material_id: UUID
    title: str
    url: str
    source_name: str
    published_at: datetime
    description: str
    categories: list[Category]
    popularity: dict[str, float]
    media_type: Literal["image", "video", "none"] = "none"
    media_url: str | None = None
    thumbnail_url: str | None = None
    media_source: str | None = None
    independent_mentions: int
    score: float
    score_reasons: list[str]
    discovery_score: float = 0.0
    discovery_reasons: list[str] = Field(default_factory=list)
    hidden_gem: bool = False
    rising: bool = False
    editorial_fit: float = Field(default=0.0, ge=0.0, le=100.0)
    editorial_reasons: list[str] = Field(default_factory=list)
    delivery_score: float = Field(default=0.0, ge=0.0, le=100.0)
    urgent: bool = False


class EditorialCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    material: RankedMaterial
    enrichment: EditorialEnrichment
    llm_model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    editorial_error: str | None = None


class MaterialFeedback(BaseModel):
    model_config = ConfigDict(frozen=True)

    material_id: UUID
    feedback_type: FeedbackType
    updated_at: datetime
    source_key: str
    categories: list[Category] = Field(default_factory=list)
    importance_score: float
    discovery_score: float
