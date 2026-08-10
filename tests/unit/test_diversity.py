from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from f117.domain import Category, RankedMaterial
from f117.pipeline.diversity import DiversityConfig, diversify, soft_balance_cyberculture


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


def test_culture_balance_allows_all_tech_when_no_culture_is_worthy() -> None:
    materials = [_material(f"Tech {index}", source="Tech", score=90 - index) for index in range(5)]

    assert soft_balance_cyberculture(materials, top_n=5, config=DiversityConfig())[:5] == materials


def test_culture_balance_prefers_comparable_culture_without_a_hard_quota() -> None:
    materials = [
        *[
            _material(f"Tech {index}", source=f"Tech {index}", score=90 - index)
            for index in range(5)
        ],
        *[
            _material(
                f"Culture {index}",
                source=f"Culture {index}",
                score=89 - index,
                category=Category.CYBERCULTURE,
            )
            for index in range(5)
        ],
    ]
    materials.sort(key=lambda material: material.score, reverse=True)

    selected = soft_balance_cyberculture(materials, top_n=5, config=DiversityConfig())[:5]

    assert sum(Category.CYBERCULTURE in item.categories for item in selected) == 2


def test_culture_balance_never_evicts_exceptional_tech_for_mediocre_culture() -> None:
    materials = [
        *[
            _material(f"Tech {index}", source=f"Tech {index}", score=99 - index)
            for index in range(3)
        ],
        *[
            _material(
                f"Culture {index}",
                source=f"Culture {index}",
                score=65 - index,
                category=Category.CYBERCULTURE,
            )
            for index in range(5)
        ],
    ]
    materials.sort(key=lambda material: material.score, reverse=True)

    selected = soft_balance_cyberculture(materials, top_n=5, config=DiversityConfig())[:5]

    assert {"Tech 0", "Tech 1", "Tech 2"}.issubset({item.title for item in selected})


def test_culture_balance_does_not_cap_exceptional_culture_or_urgent_cards() -> None:
    materials = [
        *[
            _material(
                f"Culture {index}",
                source=f"Culture {index}",
                score=99 - index,
                category=Category.CYBERCULTURE,
            )
            for index in range(3)
        ],
        *[
            _material(f"Tech {index}", source=f"Tech {index}", score=96 - index)
            for index in range(2)
        ],
    ]
    materials.sort(key=lambda material: material.score, reverse=True)

    selected = soft_balance_cyberculture(materials, top_n=5, config=DiversityConfig())[:5]

    assert sum(Category.CYBERCULTURE in item.categories for item in selected) == 3


def test_culture_balance_does_not_fill_with_weak_culture() -> None:
    materials = [
        *[
            _material(f"Tech {index}", source=f"Tech {index}", score=90 - index)
            for index in range(5)
        ],
        _material("Weak culture", source="Culture", score=40, category=Category.CYBERCULTURE),
    ]
    materials.sort(key=lambda material: material.score, reverse=True)

    selected = soft_balance_cyberculture(materials, top_n=5, config=DiversityConfig())[:5]

    assert all(Category.CYBERCULTURE not in item.categories for item in selected)
