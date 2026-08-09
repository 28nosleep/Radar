from __future__ import annotations

from datetime import UTC, datetime

from f117.domain import Category, NormalizedItem
from f117.pipeline.classifier import classify_item, classify_text


def _item(**updates: object) -> NormalizedItem:
    values: dict[str, object] = {
        "external_id": "1",
        "source_key": "example",
        "source_name": "Example",
        "source_reputation": 0.7,
        "title": "General technology update",
        "url": "https://example.com/item",
        "canonical_url": "https://example.com/item",
        "published_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "collected_at": datetime(2026, 8, 6, 13, tzinfo=UTC),
        "description": "No specific topic",
        "source_categories": [],
        "categories": [],
        "popularity": {},
        "content_hash": "a" * 64,
        "normalized_title": "general technology update",
    }
    values.update(updates)
    return NormalizedItem.model_validate(values)


def test_multi_label_rules_return_stable_category_order() -> None:
    categories = classify_text(
        "Open-source LLM released on GitHub",
        "A new large language model and its source code are available.",
    )

    assert categories == [Category.AI, Category.LLM, Category.OPEN_SOURCE]


def test_llm_implies_ai() -> None:
    assert classify_text("Claude gets a larger context window") == [
        Category.AI,
        Category.LLM,
    ]


def test_keywords_use_word_boundaries() -> None:
    assert classify_text("Said company updates its main website") == [Category.OTHER]


def test_funny_and_wtf_are_independent_labels() -> None:
    categories = classify_text("WTF: a hilarious robot fail", "A bizarre robot demo")

    assert categories == [Category.ROBOTICS, Category.FUNNY, Category.WTF]


def test_source_categories_are_preserved_before_keyword_results() -> None:
    item = _item(
        title="A paper introduces a language model",
        source_categories=[Category.RESEARCH],
    )

    classified = classify_item(item)

    assert classified.categories == [Category.AI, Category.LLM, Category.RESEARCH]
    assert classified.source_categories == [Category.RESEARCH]


def test_other_is_removed_when_a_source_default_is_specific() -> None:
    classified = classify_item(
        _item(source_categories=[Category.ROBOTICS]),
    )

    assert classified.categories == [Category.ROBOTICS]
