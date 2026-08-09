from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from f117.config import Settings
from f117.domain import Category, NormalizedItem, StoredMaterial
from f117.pipeline.classifier import classify_text
from f117.pipeline.editorial import EditorialConfig, assess_editorial_fit
from f117.pipeline.ranking import score_material
from f117.services.digest import DigestService, _select_for_delivery

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
CONFIG = EditorialConfig()


def _assess(
    title: str,
    *,
    description: str = "",
    source_key: str = "news",
    categories: list[Category] | None = None,
    popularity: dict[str, float] | None = None,
    qualitative_signals: list[str] | None = None,
    mentions: int = 1,
    published_at: datetime = NOW,
):
    inferred = classify_text(title, description) if categories is None else categories
    item = NormalizedItem(
        external_id=str(uuid4()),
        source_key=source_key,
        source_name=source_key,
        source_reputation=0.9,
        title=title,
        url=f"https://example.com/{uuid4()}",
        canonical_url=f"https://example.com/{uuid4()}",
        published_at=published_at,
        collected_at=NOW,
        description=description,
        source_categories=inferred,
        categories=inferred,
        popularity=popularity or {},
        qualitative_signals=qualitative_signals or [],
        content_hash=uuid4().hex,
        normalized_title=title.casefold(),
    )
    stored = StoredMaterial(id=uuid4(), item=item, independent_mentions=mentions)
    ranked = score_material(stored, now=NOW)
    assessment = assess_editorial_fit(stored, ranked, config=CONFIG)
    return (
        stored,
        ranked.model_copy(
            update={
                "editorial_fit": assessment.fit,
                "editorial_reasons": assessment.reasons,
                "delivery_score": assessment.delivery_score,
                "urgent": assessment.urgent,
            }
        ),
        assessment,
    )


@pytest.mark.parametrize(
    ("title", "source_key", "categories", "popularity", "mentions"),
    [
        ("OpenAI is testing a new frontier model", "openai-news", None, {}, 1),
        (
            "Apple releases first trailer for Neuromancer adaptation",
            "wired-culture",
            None,
            {},
            1,
        ),
        (
            "Humanoid robot demonstrates a genuinely new capability",
            "robot-news",
            None,
            {},
            1,
        ),
        ("BCI restores speech from neural signals", "science-news", None, {}, 1),
        (
            "Unknown open-source AI project jumps from 100 to 10k stars rapidly",
            "github-ai-robotics",
            [Category.AI, Category.OPEN_SOURCE],
            {"github_stars": 10_000, "github_stars_per_hour": 100},
            2,
        ),
    ],
)
def test_high_editorial_acceptance_fixtures(
    title: str,
    source_key: str,
    categories: list[Category] | None,
    popularity: dict[str, float],
    mentions: int,
) -> None:
    _, _, result = _assess(
        title,
        source_key=source_key,
        categories=categories,
        popularity=popularity,
        mentions=mentions,
    )

    assert result.eligible, result.reasons
    assert result.fit >= 72


@pytest.mark.parametrize(
    ("title", "source_key", "categories", "popularity"),
    [
        (
            "Deskcat DIY robot project",
            "github-ai-robotics",
            [Category.ROBOTICS, Category.OPEN_SOURCE],
            {"github_stars": 1, "forks": 0, "releases": 0},
        ),
        (
            "Integrated power-system planning with stability constraints",
            "arxiv-ai",
            [Category.RESEARCH],
            {},
        ),
        (
            "Enterprise Kubernetes observability update",
            "tech-news",
            [Category.AI],
            {},
        ),
        ("Celebrity joins an ordinary Netflix comedy", "wired-culture", [Category.OTHER], {}),
    ],
)
def test_low_editorial_acceptance_fixtures_are_rejected(
    title: str,
    source_key: str,
    categories: list[Category],
    popularity: dict[str, float],
) -> None:
    _, _, result = _assess(
        title,
        source_key=source_key,
        categories=categories,
        popularity=popularity,
    )

    assert not result.eligible


def test_exceptional_arxiv_and_strong_github_can_pass_source_gates() -> None:
    _, _, arxiv = _assess(
        "BCI restores speech from neural signals",
        source_key="arxiv-ai",
        categories=[Category.BRAIN_INTERFACE, Category.RESEARCH],
    )
    _, _, github = _assess(
        "Major open-source AI model release",
        source_key="github-ai-robotics",
        categories=[Category.AI, Category.OPEN_SOURCE],
        popularity={"github_stars": 5_000, "forks": 400, "releases": 1},
    )

    assert arxiv.eligible
    assert github.eligible


def test_cultural_story_outranks_narrow_technical_paper() -> None:
    _, cultural_ranked, cultural = _assess(
        "Apple releases first trailer for Neuromancer adaptation",
        source_key="wired-culture",
    )
    _, technical_ranked, technical = _assess(
        "Integrated power-system planning with stability constraints",
        source_key="arxiv-ai",
        categories=[Category.RESEARCH],
    )

    assert cultural.eligible
    assert not technical.eligible
    assert cultural_ranked.delivery_score > technical_ranked.delivery_score


def test_digest_does_not_fill_weak_slots_and_urgent_is_separate() -> None:
    strong, strong_ranked, strong_result = _assess(
        "OpenAI is testing a new frontier model",
        source_key="openai-news",
    )
    weak, weak_ranked, _ = _assess(
        "Enterprise Kubernetes observability update",
        categories=[Category.AI],
    )
    selected = _select_for_delivery(
        [strong_ranked, weak_ranked],
        [strong, weak],
        top_n=5,
        minimum_delivery_score=CONFIG.minimum_delivery_score,
        minimum_editorial_fit=CONFIG.minimum_fit,
        editorial_fit_weight=CONFIG.fit_weight,
    )
    urgent = _select_for_delivery(
        [strong_ranked, weak_ranked],
        [strong, weak],
        top_n=2,
        minimum_delivery_score=CONFIG.minimum_delivery_score,
        minimum_editorial_fit=CONFIG.minimum_fit,
        editorial_fit_weight=CONFIG.fit_weight,
        urgent_only=True,
    )

    assert strong_result.urgent
    assert [item.material_id for item in selected] == [strong.id]
    assert [item.material_id for item in urgent] == [strong.id]


def test_old_generic_youtube_robot_video_fails_freshness() -> None:
    stored, _, _ = _assess(
        "Generic humanoid robot trends video",
        source_key="youtube-ai-robotics",
        categories=[Category.ROBOTICS],
        published_at=datetime.now(UTC) - timedelta(days=90),
    )
    service = DigestService(
        settings=Settings(_env_file=None, freshness_youtube_daily_max_age_days=30),
        repository=None,  # type: ignore[arg-type]
        collector=None,  # type: ignore[arg-type]
        enricher=None,  # type: ignore[arg-type]
        notifier=None,  # type: ignore[arg-type]
    )

    assert not service._passes_base_freshness(stored)


def test_youtube_wtf_requires_current_engagement_or_cross_source_signal() -> None:
    _, _, quiet = _assess(
        "Humanoid robot's bizarre fail during a live demo",
        source_key="youtube-ai-robotics",
        categories=[Category.ROBOTICS, Category.WTF],
        popularity={"youtube_views": 800, "youtube_likes": 12},
    )
    _, _, viral = _assess(
        "Humanoid robot's bizarre fail during a live demo",
        source_key="youtube-ai-robotics",
        categories=[Category.ROBOTICS, Category.WTF],
        popularity={"youtube_views": 500_000, "youtube_likes": 15_000},
    )

    assert not quiet.eligible
    assert viral.eligible


def test_reddit_discussion_needs_both_engagement_and_editorial_story_value() -> None:
    _, _, chatter = _assess(
        "Thinking about getting a subscription, which AI model should I choose?",
        source_key="reddit-singularity",
        categories=[Category.AI, Category.LLM],
        popularity={"reddit_upvotes": 2_000, "reddit_comments": 300},
    )
    _, _, story = _assess(
        "Humanoid robot demonstrates a genuinely new capability",
        source_key="reddit-robotics",
        categories=[Category.ROBOTICS],
        popularity={"reddit_upvotes": 2_000, "reddit_comments": 300},
    )

    assert not chatter.eligible
    assert story.eligible


def test_reddit_rss_event_can_pass_without_api_metrics() -> None:
    _, _, result = _assess(
        "OpenAI announces a new frontier model",
        source_key="reddit-openai",
        categories=[Category.AI, Category.LLM],
        qualitative_signals=["reddit_rss", "reddit_seen_new"],
    )

    assert result.eligible, result.reasons
    assert any("Reddit RSS gate passed" in reason for reason in result.reasons)


def test_reddit_rss_multi_feed_presence_is_qualitative_not_fake_engagement() -> None:
    _, _, result = _assess(
        "AI model release is bizarre",
        source_key="reddit-singularity",
        categories=[Category.AI],
        qualitative_signals=["reddit_rss", "reddit_seen_new", "reddit_seen_hot"],
    )

    assert result.eligible, result.reasons
    assert any("RSS multi-feed presence: hot + new" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    ("title", "expected_reason"),
    [
        (
            "BeingBeyond collects humanoid-training data with robotic hands",
            "no standalone news",
        ),
        (
            "OpenAI’s Model Codenamed Doug will reportedly make Fable look primitive",
            "unsupported speculation",
        ),
        (
            "You simply can't trust apes with closed source ASI",
            "unsupported speculation",
        ),
        ("Benchmarking models on ability to prompt-engineer GPT-2", "no standalone news"),
        (
            "Thinking about getting a subscription, which AI model should I choose?",
            "community chatter/question",
        ),
    ],
)
def test_reddit_rss_anti_chatter_and_unverified_content_stays_rejected(
    title: str, expected_reason: str
) -> None:
    _, _, result = _assess(
        title,
        source_key="reddit-singularity",
        categories=[Category.AI, Category.LLM],
        qualitative_signals=["reddit_rss", "reddit_seen_new", "reddit_seen_hot"],
    )

    assert not result.eligible
    assert any(expected_reason in reason for reason in result.reasons)


def test_tiny_github_exceptional_concept_is_discovery_only() -> None:
    _, _, result = _assess(
        "OpenAI demonstrates a bizarre humanoid robot new capability",
        source_key="github-ai-robotics",
        categories=[Category.AI, Category.ROBOTICS, Category.OPEN_SOURCE],
        popularity={"github_stars": 1, "forks": 0, "releases": 0},
    )

    assert not result.eligible
    assert any(
        "exceptional concept retained for discovery only" in reason for reason in result.reasons
    )


def test_github_rapid_growth_still_passes_delivery_gate() -> None:
    _, _, result = _assess(
        "AI project release",
        source_key="github-ai-robotics",
        categories=[Category.AI, Category.OPEN_SOURCE],
        popularity={"github_stars": 12, "github_stars_per_hour": 30, "forks": 1},
    )

    assert result.eligible, result.reasons
