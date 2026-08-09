from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from f117.adapters.openai_editorial import (
    DeterministicEditorialEnricher,
    OpenAIEditorialEnricher,
    ResilientEditorialEnricher,
)
from f117.domain import Category, EditorialCard, EditorialEnrichment, RankedMaterial


def _material(title: str = "A new robot") -> RankedMaterial:
    return RankedMaterial(
        material_id=uuid4(),
        title=title,
        url="https://example.com/item",
        source_name="Example",
        published_at=datetime(2026, 8, 6, tzinfo=UTC),
        description="A short factual description.",
        categories=[Category.ROBOTICS],
        popularity={},
        independent_mentions=1,
        score=78.0,
        score_reasons=["freshness", "reputation"],
    )


@pytest.mark.asyncio
async def test_deterministic_editorial_fallback_is_free_and_stable() -> None:
    material = _material()
    cards = await DeterministicEditorialEnricher().enrich([material])

    assert cards[0].material == material
    assert cards[0].llm_model is None
    assert cards[0].enrichment.post_fit_score == 8


class _SometimesFailingEnricher:
    async def enrich(self, materials: Sequence[RankedMaterial]) -> list[EditorialCard]:
        material = materials[0]
        if material.title == "bad":
            raise RuntimeError("provider error")
        return [
            EditorialCard(
                material=material,
                enrichment=EditorialEnrichment(
                    title_ru="Успех",
                    summary_ru="Резюме",
                    why_important="Важно",
                    post_fit_score=9,
                ),
                llm_model="test-model",
            )
        ]


@pytest.mark.asyncio
async def test_resilient_enricher_falls_back_only_for_the_failed_item() -> None:
    good = _material("good")
    bad = _material("bad")
    enricher = ResilientEditorialEnricher(
        _SometimesFailingEnricher(), DeterministicEditorialEnricher()
    )

    cards = await enricher.enrich([good, bad])

    assert cards[0].llm_model == "test-model"
    assert cards[1].llm_model is None
    assert cards[1].enrichment.title_ru == "bad"


class _FakeResponses:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> Any:
        self.arguments = kwargs
        return SimpleNamespace(
            output_parsed=EditorialEnrichment(
                title_ru="Новый робот",
                summary_ru="Кратко",
                why_important="Новая возможность",
                post_fit_score=8,
            ),
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
            model="gpt-test",
        )


@pytest.mark.asyncio
async def test_openai_adapter_uses_structured_responses_without_a_live_call() -> None:
    fake_responses = _FakeResponses()
    enricher = OpenAIEditorialEnricher(
        api_key="test-key",
        model="gpt-test",
        reasoning_effort="low",
        max_output_tokens=500,
        max_concurrency=2,
    )
    enricher.client = cast(Any, SimpleNamespace(responses=fake_responses))

    card = (await enricher.enrich([_material()]))[0]

    assert card.llm_model == "gpt-test"
    assert card.usage == {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    assert fake_responses.arguments["text_format"] is EditorialEnrichment
    assert fake_responses.arguments["store"] is False
    assert fake_responses.arguments["reasoning"] == {"effort": "low"}
