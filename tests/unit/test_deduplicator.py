from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from f117.domain import NormalizedItem, StoredMaterial
from f117.pipeline.deduplicator import (
    find_duplicate,
    is_probable_duplicate,
    title_similarity,
)
from f117.pipeline.normalizer import normalize_title

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)


def _item(**updates: object) -> NormalizedItem:
    title = str(
        updates.get(
            "title",
            "OpenAI releases a new reasoning model for software developers",
        )
    )
    values: dict[str, object] = {
        "external_id": "1",
        "source_key": "source-a",
        "source_name": "Source A",
        "source_reputation": 0.8,
        "title": title,
        "url": "https://example.com/story",
        "canonical_url": "https://example.com/story",
        "published_at": NOW,
        "collected_at": NOW,
        "description": "A sufficiently detailed description of the release and its capabilities.",
        "source_categories": [],
        "categories": [],
        "popularity": {},
        "content_hash": "a" * 64,
        "normalized_title": normalize_title(title),
    }
    values.update(updates)
    if "title" in updates and "normalized_title" not in updates:
        values["normalized_title"] = normalize_title(title)
    return NormalizedItem.model_validate(values)


def _stored(item: NormalizedItem, number: int = 1) -> StoredMaterial:
    return StoredMaterial(id=UUID(int=number), item=item)


def test_same_canonical_url_is_an_obvious_duplicate() -> None:
    first = _item(title="One title")
    second = _item(
        source_key="source-b",
        external_id="2",
        title="A completely rewritten headline",
    )

    assert is_probable_duplicate(first, second)


def test_title_similarity_is_normalized_and_explainable() -> None:
    assert title_similarity("GPT-5: A New Model!", "gpt 5 — a new model") == 1.0
    assert (
        title_similarity(
            "OpenAI releases GPT-5 for software developers worldwide",
            "OpenAI releases GPT-5 for software developers today worldwide",
        )
        > 0.92
    )


def test_uncertain_related_events_are_not_merged() -> None:
    first = _item(
        canonical_url="https://one.example/launch",
        title="Figure unveils a new humanoid robot for factory work",
        content_hash="a" * 64,
    )
    second = _item(
        canonical_url="https://two.example/funding",
        source_key="source-b",
        external_id="2",
        title="Figure raises new funding to build humanoid robots",
        content_hash="b" * 64,
    )

    assert title_similarity(first.title, second.title) < 0.92
    assert not is_probable_duplicate(first, second)


def test_short_generic_titles_are_kept_separate() -> None:
    first = _item(
        canonical_url="https://one.example/notes",
        title="Weekly AI roundup",
        content_hash="c" * 64,
    )
    second = _item(
        canonical_url="https://two.example/notes",
        source_key="source-b",
        external_id="2",
        title="Weekly AI roundup",
        content_hash="c" * 64,
    )

    assert not is_probable_duplicate(first, second)


def test_same_title_outside_time_window_is_not_merged() -> None:
    first = _item(canonical_url="https://one.example/story")
    second = _item(
        canonical_url="https://two.example/story",
        source_key="source-b",
        external_id="2",
        published_at=NOW + timedelta(days=8),
    )

    assert not is_probable_duplicate(first, second)


def test_find_duplicate_returns_strongest_match_not_first_match() -> None:
    candidate = _item(source_key="candidate", external_id="candidate")
    title_match = _item(
        canonical_url="https://other.example/story",
        source_key="source-b",
        external_id="2",
    )
    url_match = _item(
        source_key="source-c",
        external_id="3",
        title="A different headline for the exact URL",
        content_hash="b" * 64,
    )

    result = find_duplicate(candidate, [_stored(title_match, 1), _stored(url_match, 2)])

    assert result is not None
    assert result.id == UUID(int=2)


@pytest.mark.parametrize(
    ("first_title", "second_title"),
    [
        ("Model is not available in Europe", "Model is available in Europe"),
        ("Startup reports 90% faster inference", "Startup reports 30% faster inference"),
        ("Project releases version 2.0", "Project releases version 3.0"),
        ("Project raises funding round", "Project releases a new version"),
    ],
)
def test_conflicting_facts_never_fuzzy_merge(first_title: str, second_title: str) -> None:
    first = _item(title=first_title, canonical_url="https://one.example/project")
    second = _item(
        title=second_title,
        canonical_url="https://two.example/project",
        source_key="source-b",
        external_id="2",
    )

    assert not is_probable_duplicate(first, second)


def test_same_project_url_with_distinct_events_is_not_merged() -> None:
    first = _item(title="Acme project releases version 2.0")
    second = _item(
        title="Acme project raises funding round",
        source_key="source-b",
        external_id="2",
    )

    assert not is_probable_duplicate(first, second)


@pytest.mark.parametrize(
    ("first_title", "second_title"),
    [
        ("Company raises $100M for AI robotics", "Company raises $200M for AI robotics"),
        ("Inference is now 10x faster for AI teams", "Inference is now 20x faster for AI teams"),
        (
            "Model adds 128k context window for developers",
            "Model adds 256k context window for developers",
        ),
        ("Platform reaches 1M active AI users", "Platform reaches 2M active AI users"),
    ],
)
def test_comparable_numeric_claims_block_merge(first_title: str, second_title: str) -> None:
    first = _item(title=first_title)
    second = _item(title=second_title, source_key="source-b", external_id="2")

    assert not is_probable_duplicate(first, second)


def test_extra_numeric_detail_does_not_block_same_event_merge() -> None:
    first = _item(title="OpenAI launches GPT-5")
    second = _item(
        title="OpenAI launches GPT-5 with 1 million token context",
        source_key="source-b",
        external_id="2",
    )

    assert is_probable_duplicate(first, second)
