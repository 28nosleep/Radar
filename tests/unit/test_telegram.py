from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import aiohttp
import pytest

from f117.adapters.telegram import (
    TELEGRAM_TEXT_LIMIT,
    DeliveryReceipt,
    TelegramError,
    TelegramFeedbackPoller,
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


def test_cyberculture_uses_its_own_clean_editorial_section_and_tag() -> None:
    rendered = render_card(_card(Category.CYBERCULTURE, title="Neuromancer"))

    assert rendered.startswith("🌐 Киберкультура\n\n")
    assert "#Cyberculture #Technology" in rendered


def test_editorial_card_is_human_readable_and_hides_debug_metrics() -> None:
    rendered = render_card(_card(Category.RESEARCH))

    assert "Почему это важно:" in rendered
    assert "Источник:" in rendered
    assert "#Research #Technology" in rendered
    assert "Теги:" not in rendered
    assert '<a href="https://example.com/item?a=1&amp;b=2">Открыть материал</a>' in rendered
    assert "\n\n<a href=" in rendered
    assert "score" not in rendered.casefold()
    assert "freshness" not in rendered
    assert rendered.startswith("📄 Исследования\n\n")
    assert "Комментарий id:28:" not in rendered
    assert "📡 <b>id:28:</b>" in rendered
    assert "&gt;_" not in rendered
    for decoration in ("сигнал принят", "аномалия обнаружена", "объект замечен", "новая сборка"):
        assert decoration not in rendered
    tags = next(line for line in rendered.splitlines() if line.startswith("#"))
    assert re.fullmatch(r"#[A-Za-z][A-Za-z0-9]*(?: #[A-Za-z][A-Za-z0-9]*){1,4}", tags)


def test_debug_card_exposes_internal_score_only_on_request() -> None:
    assert "Debug: score" in render_card(_card(Category.AI), debug=True)


def test_editorial_card_shows_growth_only_when_snapshot_data_exists() -> None:
    card = _card(Category.AI).model_copy(
        update={
            "material": _card(Category.AI).material.model_copy(
                update={"popularity": {"growth_percent": 240.0, "growth_window_hours": 4.0}}
            )
        }
    )

    assert "Набирает: +240% за 4.0 ч" in render_card(card)


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
    assert [[button["text"] for button in row] for row in reply_markup["inline_keyboard"]] == [
        ["👍 Полезно", "👎 Мимо", "⭐ В пост"]
    ]
    assert "Открыть материал" not in str(reply_markup)
    assert (
        str(cards[0].material.material_id) in reply_markup["inline_keyboard"][0][0]["callback_data"]
    )


@pytest.mark.asyncio
async def test_image_card_uses_send_photo_and_invalid_image_falls_back_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    class Response:
        def __init__(self, status: int) -> None:
            self.status = status

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            return (
                {"ok": True, "result": {"message_id": len(requests)}}
                if self.status == 200
                else {"ok": False, "description": "wrong file identifier"}
            )

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, url: str, *, json: dict[str, object]) -> Response:
            del json
            requests.append(url.rsplit("/", 1)[-1])
            return Response(400 if requests[-1] == "sendPhoto" else 200)

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession", lambda **kwargs: Session(**kwargs)
    )
    card = _card(Category.AI).model_copy(
        update={
            "material": _card(Category.AI).material.model_copy(
                update={"media_type": "image", "media_url": "https://cdn.example.com/image.jpg"}
            )
        }
    )
    await TelegramNotifier(bot_token="TOKEN", chat_id="123").send([card])

    assert requests == ["sendMessage", "sendPhoto", "sendMessage"]


@pytest.mark.asyncio
async def test_long_image_caption_uses_complete_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[tuple[str, dict[str, object]]] = []

    class Response:
        status = 200

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            return {"ok": True, "result": {"message_id": len(payloads)}}

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, url: str, *, json: dict[str, object]) -> Response:
            payloads.append((url.rsplit("/", 1)[-1], json))
            return Response()

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession", lambda **kwargs: Session(**kwargs)
    )
    card = _card(Category.AI).model_copy(
        update={
            "material": _card(Category.AI).material.model_copy(
                update={"media_type": "image", "media_url": "https://cdn.example.com/image.jpg"}
            ),
            "enrichment": _card(Category.AI).enrichment.model_copy(
                update={
                    "title_ru": "t" * 220,
                    "summary_ru": "x" * 900,
                    "why_important": "w" * 220,
                    "ironic_comment": "i" * 190,
                }
            ),
        }
    )
    rendered = render_card(card)

    await TelegramNotifier(bot_token="TOKEN", chat_id="123").send([card])

    assert [method for method, _ in payloads] == ["sendMessage", "sendMessage"]
    assert payloads[1][1]["text"] == rendered


@pytest.mark.asyncio
async def test_ambiguous_photo_send_does_not_issue_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    class Response:
        status = 200

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            return {"ok": True, "result": {"message_id": 1}}

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, url: str, *, json: dict[str, object]) -> Response:
            del json
            method = url.rsplit("/", 1)[-1]
            requests.append(method)
            if method == "sendPhoto":
                raise aiohttp.ClientError("connection lost")
            return Response()

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession", lambda **kwargs: Session(**kwargs)
    )
    card = _card(Category.AI).model_copy(
        update={
            "material": _card(Category.AI).material.model_copy(
                update={"media_type": "image", "media_url": "https://cdn.example.com/image.jpg"}
            )
        }
    )
    with pytest.raises(TelegramError, match="ambiguous"):
        await TelegramNotifier(bot_token="TOKEN", chat_id="123").send([card])

    assert requests == ["sendMessage", "sendPhoto"]


@pytest.mark.asyncio
async def test_youtube_card_requests_large_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict[str, object]] = []

    class Response:
        status = 200

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            return {"ok": True, "result": {"message_id": len(payloads)}}

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, _: str, *, json: dict[str, object]) -> Response:
            payloads.append(json)
            return Response()

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession", lambda **kwargs: Session(**kwargs)
    )
    card = _card(Category.AI).model_copy(
        update={
            "material": _card(Category.AI).material.model_copy(
                update={"url": "https://www.youtube.com/watch?v=video", "media_type": "video"}
            )
        }
    )
    await TelegramNotifier(bot_token="TOKEN", chat_id="123").send([card])

    assert payloads[1]["link_preview_options"] == {
        "is_disabled": False,
        "url": "https://www.youtube.com/watch?v=video",
        "prefer_large_media": True,
        "show_above_text": True,
    }


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


@pytest.mark.asyncio
async def test_feedback_poller_processes_owner_callback_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_id = uuid4()
    requests: list[tuple[str, dict[str, object]]] = []

    class Store:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def record_feedback(self, **values: object) -> object:
            self.calls.append(values)
            return object()

        async def latest_telegram_update_id(self) -> int | None:
            return None

        async def mark_telegram_update_processed(self, update_id: int) -> None:
            assert update_id == 100

    class Response:
        status = 200

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            if len(requests) == 1:
                return {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 99,
                            "callback_query": {
                                "id": "callback-1",
                                "data": f"feedback:{material_id}:useful",
                                "message": {"chat": {"id": 123}},
                            },
                        },
                        {
                            "update_id": 100,
                            "callback_query": {
                                "id": "foreign",
                                "data": f"feedback:{material_id}:miss",
                                "message": {"chat": {"id": 999}},
                            },
                        },
                    ],
                }
            return {"ok": True, "result": True}

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, url: str, *, json: dict[str, object]) -> Response:
            requests.append((url, json))
            return Response()

    monkeypatch.setattr(
        "f117.adapters.telegram.aiohttp.ClientSession", lambda **kwargs: Session(**kwargs)
    )
    store = Store()
    processed = await TelegramFeedbackPoller(
        bot_token="token", chat_id="123", store=store, api_base="https://telegram.invalid"
    ).poll_once()

    assert processed == 1
    assert store.calls == [
        {"material_id": material_id, "feedback_type": "useful", "telegram_update_id": 99}
    ]
    assert requests[1][0].endswith("/answerCallbackQuery")
