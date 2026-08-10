from __future__ import annotations

import pytest

from f117.domain import AIVerdict, Category, EditorialEnrichment
from f117.pipeline.editorial_output import normalize_ai_opinion
from f117.services.digest import should_deliver_card
from tests.unit.test_telegram import _card


def _valid_opinion() -> str:
    return (
        "Компания показывает эффектный прототип, но не приводит независимых измерений его "
        "надёжности и реального внедрения. Поэтому громкий заголовок пока сильнее фактической "
        "базы, а практическую ценность рано считать доказанной. Это любопытный сигнал для "
        "наблюдения, но не повод принимать маркетинговые обещания за готовый продукт."
    )


@pytest.mark.parametrize("ending", ["...", "…", ",", ":", ";"])
def test_ai_opinion_never_accepts_unfinished_ending(ending: str) -> None:
    with pytest.raises(ValueError):
        normalize_ai_opinion(_valid_opinion()[:-1] + ending)


def test_ai_verdict_schema_has_no_legacy_commentary_fields() -> None:
    schema = EditorialEnrichment.model_json_schema()["properties"]

    assert "ai_verdict" in schema
    assert "ai_opinion" in schema
    assert "why_important" not in schema
    assert "ironic_comment" not in schema


@pytest.mark.parametrize("verdict", list(AIVerdict))
def test_manual_submission_is_shown_for_every_ai_verdict(verdict: AIVerdict) -> None:
    card = _card(Category.AI).model_copy(
        update={
            "enrichment": EditorialEnrichment(
                title_ru="Заголовок",
                summary_ru="Описание",
                ai_opinion=_valid_opinion(),
                ai_verdict=verdict,
                post_fit_score=1,
            )
        }
    )

    assert should_deliver_card(card, manual=True) is True


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (AIVerdict.STRONG, True),
        (AIVerdict.INTERESTING, True),
        (AIVerdict.WEAK, False),
        (AIVerdict.HYPE, False),
        (AIVerdict.SKIP, False),
    ],
)
def test_automatic_delivery_uses_strict_ai_verdict_policy(
    verdict: AIVerdict, expected: bool
) -> None:
    enrichment = EditorialEnrichment(
        title_ru="Заголовок",
        summary_ru="Описание",
        ai_opinion=_valid_opinion(),
        ai_verdict=verdict,
        post_fit_score=3,
    )
    tech = _card(Category.AI).model_copy(update={"enrichment": enrichment})
    culture = _card(Category.CYBERCULTURE).model_copy(update={"enrichment": enrichment})

    assert should_deliver_card(tech, manual=False) is expected
    assert should_deliver_card(culture, manual=False) is expected
