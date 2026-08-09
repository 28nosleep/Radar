from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from f117.domain import Category, RankedMaterial
from f117.pipeline.diversity import DiversityConfig, diversify


def _material(
    title: str, *, source: str, score: float, category: Category = Category.AI
) -> RankedMaterial:
    return RankedMaterial(
        material_id=uuid4(),
        title=title,
        url=f"https://example.com/{uuid4()}",
        source_name=source,
        published_at=datetime.now(UTC),
        description="",
        categories=[category],
        popularity={},
        independent_mentions=1,
        score=score,
        score_reasons=[],
    )


def test_diversity_prefers_other_sources_before_deferred_same_source_items() -> None:
    materials = [
        _material("OpenAI item one", source="OpenAI Blog", score=70),
        _material("OpenAI item two", source="OpenAI Blog", score=69),
        _material("OpenAI item three", source="OpenAI Blog", score=68),
        _material("Robotics research", source="arXiv", score=67, category=Category.RESEARCH),
    ]

    result = diversify(materials, config=DiversityConfig(max_per_source=2, max_per_entity=10))

    assert [item.source_name for item in result[:3]] == ["OpenAI Blog", "OpenAI Blog", "arXiv"]


def test_diversity_never_discards_a_strong_material() -> None:
    materials = [
        _material("OpenAI one", source="OpenAI Blog", score=80),
        _material("OpenAI major release", source="OpenAI Blog", score=95),
    ]

    result = diversify(
        materials, config=DiversityConfig(max_per_source=1, strong_score_threshold=85)
    )

    assert result == materials


def test_diversity_does_not_replace_a_clear_winner_with_weak_filler() -> None:
    materials = [
        _material("OpenAI release", source="OpenAI Blog", score=82),
        _material("OpenAI technical report", source="OpenAI Blog", score=78),
        _material("Weak filler", source="Other", score=45),
    ]

    result = diversify(materials, config=DiversityConfig(max_per_source=1, close_score_gap=8))

    assert result[:2] == materials[:2]


def test_diversity_preserves_high_deferred_before_later_weaker_overflow() -> None:
    materials = [
        _material("A 82", source="A", score=82),
        _material("A 78", source="A", score=78),
        _material("B 76", source="B", score=76),
        _material("A 70", source="A", score=70),
    ]

    result = diversify(materials, config=DiversityConfig(max_per_source=1))

    assert [item.score for item in result[:3]] == [82, 76, 78]
