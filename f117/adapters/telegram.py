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
TELEGRAM_CAPTION_BUDGET = 900
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
                session, f"<b>id:28</b> · {len(cards)} материалов", disable_preview=True
            )
            for card, rendered in rendered_cards:
                if on_delivering is not None:
                    await on_delivering(card.material.material_id)
                try:
                    message_id = await self._send_card(session, card, rendered)
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

    async def _send_card(
        self, session: aiohttp.ClientSession, card: EditorialCard, rendered: str
    ) -> str:
        material = card.material
        if _is_youtube_url(material.url):
            try:
                return await self._send_message(
                    session,
                    rendered,
                    disable_preview=False,
                    link_url=material.url,
                    material_id=material.material_id,
                    preview_url=material.url,
                )
            except TelegramError as exc:
                if (
                    exc.ambiguous
                    or exc.retry_after_seconds is not None
                    or not material.thumbnail_url
                    or not _is_preview_validation_error(exc)
                ):
                    raise
                thumbnail = material.thumbnail_url
                assert thumbnail is not None
                return await self._send_photo(session, card, rendered, thumbnail)
        image_url = material.media_url or material.thumbnail_url
        if material.media_type == "image" and _is_safe_media_url(image_url):
            assert image_url is not None
            # Telegram captions are deliberately kept smaller than text messages.
            # Preserve the complete editorial card through the existing text route
            # rather than trimming an important section just to attach media.
            if len(rendered) > TELEGRAM_CAPTION_BUDGET:
                return await self._send_message(
                    session,
                    rendered,
                    disable_preview=True,
                    link_url=material.url,
                    material_id=material.material_id,
                )
            try:
                return await self._send_photo(session, card, rendered, image_url)
            except TelegramError as exc:
                # A confirmed media validation error is safe to degrade. An uncertain
                # request may already have posted and must preserve the normal hold.
                if (
                    exc.ambiguous
                    or exc.retry_after_seconds is not None
                    or not _is_media_validation_error(exc)
                ):
                    raise
        return await self._send_message(
            session,
            rendered,
            disable_preview=True,
            link_url=material.url,
            material_id=material.material_id,
        )

    async def _send_photo(
        self, session: aiohttp.ClientSession, card: EditorialCard, caption: str, photo: str
    ) -> str:
        if len(caption) > TELEGRAM_CAPTION_BUDGET:
            raise ValueError("Rendered Telegram media caption exceeds the safe budget")
        return await self._send_request(
            session,
            "sendPhoto",
            {
                "chat_id": self.chat_id,
                "photo": photo,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": _reply_markup(card.material.material_id, card.material.url),
            },
        )

    async def _send_message(
        self,
        session: aiohttp.ClientSession,
        text: str,
        *,
        disable_preview: bool,
        link_url: str | None = None,
        material_id: UUID | None = None,
        preview_url: str | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        if link_url is not None and material_id is not None:
            payload["reply_markup"] = _reply_markup(material_id, link_url)
        if preview_url is not None:
            payload["link_preview_options"] = {
                "is_disabled": False,
                "url": preview_url,
                "prefer_large_media": True,
                "show_above_text": True,
            }
        return await self._send_request(session, "sendMessage", payload)

    async def _send_request(
        self, session: aiohttp.ClientSession, method: str, payload: dict[str, object]
    ) -> str:
        for attempt in range(3):
            await self._pace()
            try:
                async with session.post(f"{self.api_root}/{method}", json=payload) as response:
                    response_payload = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise TelegramError(
                    f"Telegram {method} response is ambiguous: {exc}", ambiguous=True
                ) from exc
            if not isinstance(response_payload, dict):
                raise TelegramError(f"Telegram {method} returned a non-object response")
            if response.status == 429 or response_payload.get("error_code") == 429:
                retry_after = _retry_after(response_payload) or 1
                if attempt < 2:
                    await asyncio.sleep(retry_after)
                    continue
                raise TelegramError(
                    f"Telegram {method} rate limit exhausted",
                    retry_after_seconds=retry_after,
                )
            if response.status != 200 or not response_payload.get("ok"):
                description = response_payload.get("description", f"HTTP {response.status}")
                raise TelegramError(f"Telegram {method} failed: {description}")
            result = response_payload.get("result")
            if not isinstance(result, dict) or "message_id" not in result:
                raise TelegramError(f"Telegram {method} returned no message_id", ambiguous=True)
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
        "🌐 Киберкультура",
        "😂 Смешное и WTF",
    ]
    return {section: grouped[section] for section in order if grouped[section]}


def render_card(card: EditorialCard, *, section: str | None = None, debug: bool = False) -> str:
    material = card.material
    enrichment = card.enrichment
    hashtags = _hashtags(material.categories)
    link = html.escape(_telegram_url(material.url), quote=True)
    rendered = (
        f"{_escape_bounded(section or _section_for(card), 80)}\n\n"
        f"<b>{_escape_bounded(enrichment.title_ru, 220)}</b>\n\n"
        f"{_escape_bounded(enrichment.summary_ru, 380)}\n\n"
        f"<b>Почему это важно:</b> {_escape_bounded(enrichment.why_important, 220)}\n\n"
        f"📡 <b>id:28:</b> {_escape_bounded(_id28_comment(enrichment.ironic_comment), 190)}\n\n"
        f"Источник: {_escape_bounded(material.source_name, 160)}\n\n"
        f"{hashtags}\n\n"
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
    chunks = [f"id:28 — Intelligence Engine · {len(cards)} материалов"]
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
    if Category.CYBERCULTURE in categories:
        return "🌐 Киберкультура"
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


_CATEGORY_HASHTAGS: dict[Category, str] = {
    Category.AI: "#AI",
    Category.LLM: "#LLM",
    Category.ROBOTICS: "#Robotics",
    Category.RESEARCH: "#Research",
    Category.OPEN_SOURCE: "#OpenSource",
    Category.HARDWARE: "#Hardware",
    Category.BRAIN_INTERFACE: "#BrainComputerInterface",
    Category.CYBERCULTURE: "#Cyberculture",
    Category.FUNNY: "#Funny",
    Category.WTF: "#WTF",
    Category.OTHER: "#Technology",
}


def _hashtags(categories: Sequence[Category]) -> str:
    """Render a small, stable controlled vocabulary of ASCII Telegram hashtags."""

    tags = list(
        dict.fromkeys(_CATEGORY_HASHTAGS.get(category, "#Technology") for category in categories)
    )
    if len(tags) < 2:
        tags.append("#Technology" if "#Technology" not in tags else "#AI")
    return " ".join(tags[:5])


def _growth_line(metrics: dict[str, float]) -> str | None:
    percent = metrics.get("growth_percent")
    hours = metrics.get("growth_window_hours")
    if percent is None or hours is None or percent <= 0 or hours <= 0:
        return None
    return f"Набирает: +{percent:.0f}% за {hours:.1f} ч"


def _id28_comment(value: str) -> str:
    comment = value.strip()
    if comment.casefold().startswith("id:28:"):
        return comment[len("id:28:") :].strip()
    return comment


def _reply_markup(material_id: UUID, link_url: str) -> dict[str, object]:
    del link_url
    return {
        "inline_keyboard": [
            [
                {"text": "👍 Полезно", "callback_data": f"feedback:{material_id}:useful"},
                {"text": "👎 Мимо", "callback_data": f"feedback:{material_id}:miss"},
                {"text": "⭐ В пост", "callback_data": f"feedback:{material_id}:post"},
            ],
        ]
    }


def _is_youtube_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host.casefold() in {"youtube.com", "www.youtube.com", "youtu.be"}


def _is_safe_media_url(url: str | None) -> bool:
    if not url or len(url) > TELEGRAM_URL_LIMIT:
        return False
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_media_validation_error(error: TelegramError) -> bool:
    message = str(error).casefold()
    return any(
        token in message
        for token in ("wrong file", "failed to get http url", "invalid photo", "photo url")
    )


def _is_preview_validation_error(error: TelegramError) -> bool:
    message = str(error).casefold()
    return "link preview" in message or "link_preview" in message


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
