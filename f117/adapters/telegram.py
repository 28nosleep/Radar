from __future__ import annotations

import asyncio
import html
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import aiohttp

from f117.domain import Category, EditorialCard, FeedbackType

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_URL_LIMIT = 2048
TELEGRAM_DIGEST_URL_LIMIT = 512


class TelegramError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        ambiguous: bool = False,
        material_id: UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.ambiguous = ambiguous
        self.material_id = material_id


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    material_id: UUID
    message_id: str | None


@dataclass(frozen=True, slots=True)
class TelegramFeedbackCallback:
    update_id: int
    callback_id: str
    material_id: UUID
    feedback_type: FeedbackType


class FeedbackStore(Protocol):
    async def latest_telegram_update_id(self) -> int | None: ...

    async def mark_telegram_update_processed(self, update_id: int) -> None: ...

    async def record_feedback(
        self,
        *,
        material_id: UUID,
        feedback_type: FeedbackType,
        telegram_update_id: int | None = None,
    ) -> object: ...


DeliveryCallback = Callable[[DeliveryReceipt], Awaitable[None]]
DeliveryStartCallback = Callable[[UUID], Awaitable[None]]


class DigestNotifier(Protocol):
    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivering: DeliveryStartCallback | None = None,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]: ...


class DryRunNotifier:
    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivering: DeliveryStartCallback | None = None,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]:
        del on_delivering, on_delivered
        print(render_digest(cards))
        return [
            DeliveryReceipt(material_id=card.material.material_id, message_id=None)
            for card in cards
        ]


class TelegramNotifier:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 20.0,
        debug: bool = False,
        pace_seconds: float = 0.25,
    ) -> None:
        if not chat_id.isdigit() or int(chat_id) <= 0:
            raise ValueError("F117_TELEGRAM_CHAT_ID must be a positive numeric private-chat ID")
        self.url = f"{api_base.rstrip('/')}/bot{bot_token}/sendMessage"
        self.api_root = f"{api_base.rstrip('/')}/bot{bot_token}"
        self.chat_id = chat_id
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.debug = debug
        self.pace_seconds = pace_seconds
        self._last_send_at = 0.0

    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivering: DeliveryStartCallback | None = None,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]:
        if not cards:
            return []
        # Render all cards before the first Telegram request. One malformed cached
        # card must never result in a partial batch headed by an orphan digest title.
        rendered_cards = [
            (card, render_card(card, section=section, debug=self.debug))
            for section, section_cards in group_cards(cards).items()
            for card in section_cards
        ]
        receipts: list[DeliveryReceipt] = []
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            await self._send_message(
                session, f"<b>Radar</b> · {len(cards)} материалов", disable_preview=True
            )
            for card, rendered in rendered_cards:
                if on_delivering is not None:
                    await on_delivering(card.material.material_id)
                try:
                    message_id = await self._send_message(
                        session,
                        rendered,
                        disable_preview=True,
                        link_url=card.material.url,
                        material_id=card.material.material_id,
                    )
                except TelegramError as exc:
                    exc.material_id = card.material.material_id
                    raise
                receipt = DeliveryReceipt(
                    material_id=card.material.material_id,
                    message_id=message_id,
                )
                receipts.append(receipt)
                if on_delivered is not None:
                    await on_delivered(receipt)
        return receipts

    async def _send_message(
        self,
        session: aiohttp.ClientSession,
        text: str,
        *,
        disable_preview: bool,
        link_url: str | None = None,
        material_id: UUID | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        if link_url is not None and material_id is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": "Открыть материал", "url": link_url}],
                    [
                        {
                            "text": "👍 Полезно",
                            "callback_data": f"feedback:{material_id}:useful",
                        },
                        {
                            "text": "👎 Мимо",
                            "callback_data": f"feedback:{material_id}:miss",
                        },
                        {
                            "text": "⭐ В пост",
                            "callback_data": f"feedback:{material_id}:post",
                        },
                    ],
                ]
            }
        for attempt in range(3):
            await self._pace()
            try:
                async with session.post(self.url, json=payload) as response:
                    response_payload = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise TelegramError(
                    f"Telegram sendMessage response is ambiguous: {exc}", ambiguous=True
                ) from exc
            if not isinstance(response_payload, dict):
                raise TelegramError("Telegram sendMessage returned a non-object response")
            if response.status == 429 or response_payload.get("error_code") == 429:
                retry_after = _retry_after(response_payload) or 1
                if attempt < 2:
                    await asyncio.sleep(retry_after)
                    continue
                raise TelegramError(
                    "Telegram sendMessage rate limit exhausted",
                    retry_after_seconds=retry_after,
                )
            if response.status != 200 or not response_payload.get("ok"):
                description = response_payload.get("description", f"HTTP {response.status}")
                raise TelegramError(f"Telegram sendMessage failed: {description}")
            result = response_payload.get("result")
            if not isinstance(result, dict) or "message_id" not in result:
                raise TelegramError("Telegram sendMessage returned no message_id", ambiguous=True)
            return str(result["message_id"])
        raise AssertionError("unreachable")

    async def _pace(self) -> None:
        delay = self.pace_seconds - (monotonic() - self._last_send_at)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_send_at = monotonic()


class TelegramFeedbackPoller:
    """One bounded long-poll pass for owner-only inline feedback callbacks."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        store: FeedbackStore,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 20.0,
    ) -> None:
        if not chat_id.isdigit() or int(chat_id) <= 0:
            raise ValueError("F117_TELEGRAM_CHAT_ID must be a positive numeric private-chat ID")
        self.api_root = f"{api_base.rstrip('/')}/bot{bot_token}"
        self.chat_id = chat_id
        self.store = store
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def poll_once(self) -> int:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            request: dict[str, object] = {"allowed_updates": ["callback_query"]}
            if latest_update_id := await self.store.latest_telegram_update_id():
                request["offset"] = latest_update_id + 1
            payload = await self._request(session, "getUpdates", request)
            updates = payload.get("result", [])
            if not isinstance(updates, list):
                raise TelegramError("Telegram getUpdates returned invalid result")
            processed = 0
            for update in updates:
                callback = _parse_feedback_callback(update, expected_chat_id=self.chat_id)
                if callback is None:
                    update_id = update.get("update_id") if isinstance(update, dict) else None
                    if isinstance(update_id, int):
                        await self.store.mark_telegram_update_processed(update_id)
                    continue
                saved = await self.store.record_feedback(
                    material_id=callback.material_id,
                    feedback_type=callback.feedback_type,
                    telegram_update_id=callback.update_id,
                )
                text = "Оценка сохранена" if saved is not None else "Оценка уже обработана"
                await self._request(
                    session,
                    "answerCallbackQuery",
                    {"callback_query_id": callback.callback_id, "text": text},
                )
                processed += int(saved is not None)
            return processed

    async def _request(
        self, session: aiohttp.ClientSession, method: str, payload: dict[str, object]
    ) -> dict[str, object]:
        async with session.post(f"{self.api_root}/{method}", json=payload) as response:
            response_payload = await response.json(content_type=None)
            if not isinstance(response_payload, dict):
                raise TelegramError(f"Telegram {method} returned a non-object response")
            if response.status != 200 or not response_payload.get("ok"):
                description = response_payload.get("description", f"HTTP {response.status}")
                raise TelegramError(f"Telegram {method} failed: {description}")
            return response_payload


def group_cards(cards: Sequence[EditorialCard]) -> dict[str, list[EditorialCard]]:
    grouped: dict[str, list[EditorialCard]] = defaultdict(list)
    for card in cards:
        grouped[_section_for(card)].append(card)
    order = [
        "🔥 Главное",
        "📈 Набирает популярность",
        "💎 Скрытые находки",
        "🤖 Роботы",
        "📄 Исследования",
        "🚀 Open Source",
        "😂 Смешное и WTF",
    ]
    return {section: grouped[section] for section in order if grouped[section]}


def render_card(card: EditorialCard, *, section: str | None = None, debug: bool = False) -> str:
    material = card.material
    enrichment = card.enrichment
    categories = ", ".join(_category_label(category) for category in material.categories)
    link = html.escape(_telegram_url(material.url), quote=True)
    rendered = (
        f"<b>{_escape_bounded(section or _section_for(card), 80)}</b>\n"
        f"<b>{_escape_bounded(enrichment.title_ru, 350)}</b>\n\n"
        f"{_escape_bounded(enrichment.summary_ru, 1200)}\n\n"
        f"<b>Почему это важно:</b> {_escape_bounded(enrichment.why_important, 600)}\n\n"
        f"Источник: {_escape_bounded(material.source_name, 160)}\n"
        f"Теги: {_escape_bounded(categories or _category_label(Category.OTHER), 160)}\n"
        f'<a href="{link}">Открыть материал</a>'
    )
    if growth_line := _growth_line(material.popularity):
        rendered = rendered.replace("\nИсточник:", f"\n{growth_line}\n\nИсточник:")
    if material.independent_mentions >= 2:
        rendered = rendered.replace(
            "\nИсточник:",
            "\nЗамечено сразу в "
            f"{material.independent_mentions} независимых источниках\n\nИсточник:",
        )
    if debug:
        reasons = "; ".join(material.score_reasons[:3])
        rendered += (
            f"\n\n<i>Debug: score {material.score:.1f}/100 · "
            f"discovery {material.discovery_score:.1f}/100 · "
            f"post {enrichment.post_fit_score}/10\n{_escape_bounded(reasons, 500)}</i>"
        )
    if len(rendered) > TELEGRAM_TEXT_LIMIT:
        raise ValueError("Rendered Telegram card exceeds the safe text budget")
    return rendered


def render_digest(cards: Sequence[EditorialCard]) -> str:
    chunks = [f"Radar — Intelligence Engine · {len(cards)} материалов"]
    for section, section_cards in group_cards(cards).items():
        chunks.append(f"\n{section}")
        for card in section_cards:
            chunks.append(
                f"- {card.enrichment.title_ru}\n"
                f"  {card.enrichment.summary_ru}\n"
                f"  Почему это важно: {card.enrichment.why_important}\n"
                f"  Источник: {card.material.source_name}\n"
                f"  {_display_url(card.material.url)}"
            )
    return "\n".join(chunks)


def _section_for(card: EditorialCard) -> str:
    categories = set(card.material.categories)
    popularity = sum(card.material.popularity.values())
    if Category.FUNNY in categories or Category.WTF in categories:
        return "😂 Смешное и WTF"
    if Category.ROBOTICS in categories:
        return "🤖 Роботы"
    if Category.RESEARCH in categories:
        return "📄 Исследования"
    if card.material.hidden_gem:
        return "💎 Скрытые находки"
    if Category.OPEN_SOURCE in categories:
        return "🚀 Open Source"
    if card.material.rising or card.material.independent_mentions >= 2 or popularity >= 1000:
        return "📈 Набирает популярность"
    return "🔥 Главное"


def _escape_bounded(value: str, budget: int) -> str:
    """Escape untrusted text while guaranteeing a post-escape character budget."""

    value = value.strip()
    escaped = html.escape(value)
    if len(escaped) <= budget:
        return escaped
    low = 0
    high = len(value)
    target = max(0, budget - 1)
    while low < high:
        midpoint = (low + high + 1) // 2
        if len(html.escape(value[:midpoint])) <= target:
            low = midpoint
        else:
            high = midpoint - 1
    return html.escape(value[:low].rstrip()) + "…"


def _category_label(category: Category) -> str:
    return {
        Category.AI: "ИИ",
        Category.LLM: "языковые модели",
        Category.ROBOTICS: "роботы",
        Category.RESEARCH: "исследования",
        Category.OPEN_SOURCE: "открытый код",
        Category.HARDWARE: "оборудование",
        Category.BRAIN_INTERFACE: "нейроинтерфейсы",
        Category.FUNNY: "смешное",
        Category.WTF: "необычное",
        Category.OTHER: "другое",
    }[category]


def _growth_line(metrics: dict[str, float]) -> str | None:
    percent = metrics.get("growth_percent")
    hours = metrics.get("growth_window_hours")
    if percent is None or hours is None or percent <= 0 or hours <= 0:
        return None
    return f"Набирает: +{percent:.0f}% за {hours:.1f} ч"


def _telegram_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Material URL must be an absolute HTTP(S) URL")
    if len(url) > TELEGRAM_URL_LIMIT:
        raise ValueError("Material URL exceeds Telegram's safe URL limit")
    return url


def _display_url(value: str) -> str:
    value = value.strip()
    return (
        value
        if len(value) <= TELEGRAM_DIGEST_URL_LIMIT
        else value[: TELEGRAM_DIGEST_URL_LIMIT - 1] + "…"
    )


def _retry_after(payload: dict[str, object]) -> int | None:
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    return value if isinstance(value, int) and value > 0 else None


def _parse_feedback_callback(
    update: object, *, expected_chat_id: str
) -> TelegramFeedbackCallback | None:
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    callback = update.get("callback_query")
    if not isinstance(update_id, int) or not isinstance(callback, dict):
        return None
    callback_id = callback.get("id")
    data = callback.get("data")
    message = callback.get("message")
    if (
        not isinstance(callback_id, str)
        or not isinstance(data, str)
        or not isinstance(message, dict)
    ):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or str(chat.get("id")) != expected_chat_id:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "feedback":
        return None
    try:
        return TelegramFeedbackCallback(
            update_id=update_id,
            callback_id=callback_id,
            material_id=UUID(parts[1]),
            feedback_type=FeedbackType(parts[2]),
        )
    except ValueError:
        return None
