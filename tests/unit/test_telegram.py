from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from f117.adapters.telegram import (
    TELEGRAM_TEXT_LIMIT,
    DeliveryReceipt,
    TelegramError,
    TelegramNotifier,
    group_cards,
    render_card,
)
from f117.domain import Category, EditorialCard, EditorialEnrichment, RankedMaterial


def _card(category: Category, *, title: str = "Item") -> EditorialCard:
    material = RankedMaterial(
        material_id=uuid4(),
        title=title,
        url="https://example.com/item?a=1&b=2",
        source_name="Source & Lab",
        published_at=datetime(2026, 8, 6, tzinfo=UTC),
        description="Description",
        categories=[category],
        popularity={},
        independent_mentions=1,
        score=75,
        score_reasons=["freshness < 20", "reputation"],
    )
    return EditorialCard(
        material=material,
        enrichment=EditorialEnrichment(
            title_ru=title,
            summary_ru="Summary <unsafe>",
            why_important="Because & now",
            post_fit_score=8,
        ),
    )


def test_notifier_rejects_groups_and_channels() -> None:
    with pytest.raises(ValueError, match="private-chat"):
        TelegramNotifier(bot_token="token", chat_id="-100123")


def test_render_card_escapes_untrusted_html() -> None:
    rendered = render_card(_card(Category.AI, title="<script>alert(1)</script>"))

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "Source &amp; Lab" in rendered
    assert len(rendered) <= TELEGRAM_TEXT_LIMIT


def test_group_cards_uses_required_editorial_sections() -> None:
    grouped = group_cards(
        [
            _card(Category.AI),
            _card(Category.ROBOTICS),
            _card(Category.RESEARCH),
            _card(Category.OPEN_SOURCE),
            _card(Category.WTF),
        ]
    )

    assert list(grouped) == [
        "🔥 Главное",
        "🤖 Роботы",
        "📄 Исследования",
        "🚀 Open Source",
        "😂 Смешное и WTF",
    ]


def test_editorial_card_is_human_readable_and_hides_debug_metrics() -> None:
    rendered = render_card(_card(Category.RESEARCH))

    assert "Почему это важно:" in rendered
    assert "Источник:" in rendered
    assert "Теги: исследования" in rendered
    assert "Открыть материал" in rendered
    assert "score" not in rendered.casefold()
    assert "freshness" not in rendered


def test_debug_card_exposes_internal_score_only_on_request() -> None:
    assert "Debug: score" in render_card(_card(Category.AI), debug=True)


@pytest.mark.asyncio
async def test_telegram_adapter_sends_intro_and_one_message_per_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeResponse:
        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        @property
        def status(self) -> int:
            return 200

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            return {"ok": True, "result": {"message_id": len(requests)}}

    class FakeSession:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, _: str, *, json: dict[str, object]) -> FakeResponse:
            requests.append(json)
            return FakeResponse()

    def fake_session(**kwargs: Any) -> FakeSession:
        return FakeSession(**kwargs)

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession",
        fake_session,
    )

    notifier = TelegramNotifier(
        bot_token="TOKEN",
        chat_id="123456",
        api_base="https://telegram.invalid",
    )
    cards = [_card(Category.AI), _card(Category.ROBOTICS)]
    receipts = await notifier.send(cards)

    assert len(requests) == 3
    assert all(request["chat_id"] == "123456" for request in requests)
    assert [receipt.message_id for receipt in receipts] == ["2", "3"]
    reply_markup = requests[1]["reply_markup"]
    assert isinstance(reply_markup, dict)
    assert reply_markup["inline_keyboard"][0][0]["url"] == cards[0].material.url


@pytest.mark.asyncio
async def test_telegram_reports_each_success_before_a_later_card_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_count = 0

    class FakeResponse:
        def __init__(self, number: int) -> None:
            self.number = number
            self.status = 500 if number == 3 else 200

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            if self.status == 500:
                return {"ok": False, "description": "temporary failure"}
            return {"ok": True, "result": {"message_id": self.number}}

    class FakeSession:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, _: str, *, json: dict[str, object]) -> FakeResponse:
            nonlocal request_count
            del json
            request_count += 1
            return FakeResponse(request_count)

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession",
        lambda **kwargs: FakeSession(**kwargs),
    )
    cards = [_card(Category.AI), _card(Category.ROBOTICS)]
    delivered: list[UUID] = []

    async def remember(receipt: DeliveryReceipt) -> None:
        delivered.append(receipt.material_id)

    with pytest.raises(TelegramError, match="temporary failure"):
        await TelegramNotifier(bot_token="TOKEN", chat_id="123").send(
            cards,
            on_delivered=remember,
        )

    assert delivered == [cards[0].material.material_id]
