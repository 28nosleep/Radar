from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from f117.config import Settings
from f117.domain import Category, MetricSnapshot, RankedMaterial, StoredMaterial
from f117.pipeline.discovery import DiscoveryConfig, assess_discovery
from f117.pipeline.diversity import DiversityConfig
from f117.pipeline.editorial import EditorialAssessment, EditorialConfig, assess_editorial_fit
from f117.pipeline.ranking import RankingConfig, score_material
from f117.services.digest import _select_for_delivery


@dataclass(frozen=True, slots=True)
class HistoricalReplayDecision:
    stored: StoredMaterial
    ranked: RankedMaterial
    editorial: EditorialAssessment


@dataclass(frozen=True, slots=True)
class HistoricalReplayResult:
    as_of: datetime
    collected_count: int
    rejected_future_observation_count: int
    rejected_freshness_count: int
    rejected_duplicate_count: int
    rejected_editorial_gate_count: int
    ranked_count: int
    finalists: tuple[HistoricalReplayDecision, ...]


def run_deterministic_historical_replay(
    materials: Sequence[StoredMaterial],
    histories: Mapping[UUID, Sequence[MetricSnapshot]],
    *,
    as_of: datetime,
    settings: Settings,
    max_finalists: int = 20,
    mentions_as_of: Mapping[UUID, int] | None = None,
    qualitative_signals_as_of: Mapping[UUID, Sequence[str]] | None = None,
) -> HistoricalReplayResult:
    """Replay deterministic Radar selection in memory with a strict historical cutoff.

    The function has no repository or notifier dependency and cannot mutate delivery,
    feedback, ranking, or Telegram state. Mutable observations are rebuilt only from
    snapshots at or before ``as_of``. Persisted enrichment and delivery state are ignored.
    """

    cutoff = _as_utc(as_of)
    mentions = mentions_as_of or {}
    signals = qualitative_signals_as_of or {}
    observed: list[StoredMaterial] = []
    rejected_future = 0
    rejected_freshness = 0

    for material in materials:
        if _as_utc(material.item.collected_at) > cutoff:
            rejected_future += 1
            continue
        history = [
            snapshot
            for snapshot in histories.get(material.id, ())
            if _as_utc(snapshot.captured_at) <= cutoff
        ]
        popularity = dict(history[-1].metrics) if history else {}
        historical = material.model_copy(
            update={
                "item": material.item.model_copy(
                    update={
                        "popularity": popularity,
                        "qualitative_signals": list(signals.get(material.id, ())),
                    }
                ),
                "independent_mentions": max(1, mentions.get(material.id, 1)),
                "delivered_at": None,
                "llm_enrichment": None,
                "llm_model": None,
                "llm_usage": {},
                "editorial_attempts": 0,
                "editorial_retry_at": None,
                "delivery_started_at": None,
                "delivery_ambiguous_at": None,
            }
        )
        if not _passes_base_freshness(historical, cutoff, settings):
            rejected_freshness += 1
            continue
        observed.append(historical)

    roots = [material for material in observed if material.duplicate_of_id is None]
    rejected_duplicates = len(observed) - len(roots)
    ranking_config = _ranking_config(settings)
    discovery_config = _discovery_config(settings)
    editorial_config = _editorial_config(settings)
    ranked: list[RankedMaterial] = []
    decisions: dict[UUID, HistoricalReplayDecision] = {}
    rejected_editorial = 0

    for material in roots:
        history = [
            snapshot
            for snapshot in histories.get(material.id, ())
            if _as_utc(snapshot.captured_at) <= cutoff
        ]
        importance = score_material(material, now=cutoff, config=ranking_config)
        discovery = assess_discovery(
            material,
            history,
            now=cutoff,
            config=discovery_config,
        )
        importance = importance.model_copy(
            update={
                "discovery_score": discovery.score,
                "discovery_reasons": discovery.reasons,
                "hidden_gem": discovery.hidden_gem,
                "rising": discovery.score >= settings.discovery_rising_threshold,
            }
        )
        editorial = assess_editorial_fit(material, importance, config=editorial_config)
        importance = importance.model_copy(
            update={
                "editorial_fit": editorial.fit,
                "editorial_reasons": editorial.reasons,
                "delivery_score": editorial.delivery_score,
                "urgent": editorial.urgent,
            }
        )
        if not editorial.eligible:
            rejected_editorial += 1
            continue
        ranked.append(importance)
        decisions[material.id] = HistoricalReplayDecision(material, importance, editorial)

    finalists = _select_for_delivery(
        ranked,
        roots,
        top_n=min(20, max(0, max_finalists)),
        discovery_selection_boost=settings.discovery_selection_boost,
        editorial_retry_slots=0,
        diversity_config=DiversityConfig(
            max_per_source=settings.diversity_max_per_source,
            max_per_entity=settings.diversity_max_per_entity,
            max_per_category=settings.diversity_max_per_category,
            strong_score_threshold=settings.diversity_strong_score_threshold,
            discovery_selection_boost=settings.discovery_selection_boost,
            editorial_fit_weight=settings.editorial_fit_weight,
        ),
        minimum_delivery_score=settings.minimum_delivery_score,
        minimum_editorial_fit=settings.minimum_editorial_fit,
        editorial_fit_weight=settings.editorial_fit_weight,
    )
    return HistoricalReplayResult(
        as_of=cutoff,
        collected_count=len(materials),
        rejected_future_observation_count=rejected_future,
        rejected_freshness_count=rejected_freshness,
        rejected_duplicate_count=rejected_duplicates,
        rejected_editorial_gate_count=rejected_editorial,
        ranked_count=len(ranked),
        finalists=tuple(decisions[item.material_id] for item in finalists),
    )


def _passes_base_freshness(material: StoredMaterial, as_of: datetime, settings: Settings) -> bool:
    published_at = _as_utc(material.item.published_at)
    if published_at > as_of:
        return False
    if material.item.source_key.startswith("github-"):
        return True
    age_days = (as_of - published_at).total_seconds() / 86_400
    categories = set(material.item.categories)
    if Category.FUNNY in categories or Category.WTF in categories:
        return age_days <= settings.freshness_funny_wtf_max_age_days
    if material.item.source_key.startswith("youtube-"):
        return age_days <= settings.freshness_youtube_daily_max_age_days
    return age_days <= settings.freshness_daily_max_age_days


def _ranking_config(settings: Settings) -> RankingConfig:
    return RankingConfig(
        freshness_weight=settings.rank_freshness_weight,
        reputation_weight=settings.rank_reputation_weight,
        mentions_weight=settings.rank_mentions_weight,
        popularity_weight=settings.rank_popularity_weight,
        growth_weight=settings.rank_growth_weight,
        novelty_weight=settings.rank_novelty_weight,
        topic_fit_weight=settings.rank_topic_fit_weight,
        unusualness_weight=settings.rank_unusualness_weight,
        freshness_half_life_hours=settings.rank_freshness_half_life_hours,
        full_mentions=settings.rank_full_mentions,
    )


def _discovery_config(settings: Settings) -> DiscoveryConfig:
    return DiscoveryConfig(
        growth_weight=settings.discovery_growth_weight,
        acceleration_weight=settings.discovery_acceleration_weight,
        diversity_weight=settings.discovery_diversity_weight,
        novelty_weight=settings.discovery_novelty_weight,
        freshness_weight=settings.discovery_freshness_weight,
        min_baseline=settings.discovery_min_baseline,
        min_growth_absolute=settings.discovery_min_growth_absolute,
        hidden_gem_max_popularity=settings.discovery_hidden_gem_max_popularity,
    )


def _editorial_config(settings: Settings) -> EditorialConfig:
    return EditorialConfig(
        fit_weight=settings.editorial_fit_weight,
        minimum_fit=settings.minimum_editorial_fit,
        minimum_delivery_score=settings.minimum_delivery_score,
        github_min_stars=settings.github_delivery_min_stars,
        github_min_star_velocity=settings.github_delivery_min_star_velocity,
        github_min_forks=settings.github_delivery_min_forks,
        github_min_mentions=settings.github_delivery_min_mentions,
        github_exceptional_fit=settings.github_exceptional_editorial_fit,
        arxiv_min_fit=settings.arxiv_min_editorial_fit,
        youtube_min_views=settings.youtube_delivery_min_views,
        youtube_min_view_velocity=settings.youtube_delivery_min_view_velocity,
        youtube_min_likes=settings.youtube_delivery_min_likes,
        youtube_min_mentions=settings.youtube_delivery_min_mentions,
        reddit_min_upvotes=settings.reddit_delivery_min_upvotes,
        reddit_min_comments=settings.reddit_delivery_min_comments,
        reddit_min_velocity=settings.reddit_delivery_min_velocity,
        reddit_min_mentions=settings.reddit_delivery_min_mentions,
        reddit_min_fit=settings.reddit_min_editorial_fit,
        reddit_rss_min_fit=settings.reddit_rss_min_editorial_fit,
        reddit_rss_exceptional_fit=settings.reddit_rss_exceptional_editorial_fit,
        urgent_min_fit=settings.urgent_min_editorial_fit,
        urgent_min_delivery_score=settings.urgent_min_delivery_score,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
