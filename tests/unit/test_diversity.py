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
