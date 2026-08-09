from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import aiohttp

from f117.domain import Category, EditorialCard

TELEGRAM_TEXT_LIMIT = 4096


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    material_id: UUID
    message_id: str | None


DeliveryCallback = Callable[[DeliveryReceipt], Awaitable[None]]


class DigestNotifier(Protocol):
    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]: ...


class DryRunNotifier:
    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]:
        del on_delivered
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
    ) -> None:
        if not chat_id.isdigit() or int(chat_id) <= 0:
            raise ValueError("F117_TELEGRAM_CHAT_ID must be a positive numeric private-chat ID")
        self.url = f"{api_base.rstrip('/')}/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]:
        if not cards:
            return []
        receipts: list[DeliveryReceipt] = []
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            await self._send_message(
                session,
                f"<b>Radar — Intelligence Engine</b> · {len(cards)} материалов",
                disable_preview=True,
            )
            for section, section_cards in group_cards(cards).items():
                for index, card in enumerate(section_cards):
                    prefix = f"<b>{html.escape(section)}</b>\n\n" if index == 0 else ""
                    message_id = await self._send_message(
                        session,
                        prefix + render_card(card),
                        disable_preview=True,
                        link_url=card.material.url,
                    )
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
    ) -> str:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        if link_url is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": "Открыть материал", "url": link_url}]]
            }
        async with session.post(
            self.url,
            json=payload,
        ) as response:
            response_payload = await response.json(content_type=None)
            if not isinstance(response_payload, dict):
                raise TelegramError("Telegram sendMessage returned a non-object response")
            if response.status != 200 or not response_payload.get("ok"):
                description = response_payload.get("description", f"HTTP {response.status}")
                raise TelegramError(f"Telegram sendMessage failed: {description}")
            result = response_payload.get("result")
            if not isinstance(result, dict) or "message_id" not in result:
                raise TelegramError("Telegram sendMessage returned no message_id")
            return str(result["message_id"])


def group_cards(cards: Sequence[EditorialCard]) -> dict[str, list[EditorialCard]]:
    grouped: dict[str, list[EditorialCard]] = defaultdict(list)
    for card in cards:
        grouped[_section_for(card)].append(card)
    order = [
        "🔥 Главное",
        "📈 Набирает популярность",
        "🤖 Роботы",
        "📄 Исследования",
        "🚀 Open Source",
        "😂 Смешное и WTF",
    ]
    return {section: grouped[section] for section in order if grouped[section]}


def render_card(card: EditorialCard) -> str:
    material = card.material
    enrichment = card.enrichment
    categories = ", ".join(category.value for category in material.categories)
    reasons = "; ".join(material.score_reasons[:3])
    rendered = (
        f"<b>{_escape_bounded(enrichment.title_ru, 350)}</b>\n"
        f"Источник: {_escape_bounded(material.source_name, 160)}\n"
        f"Оценка: <b>{material.score:.1f}/100</b> · пост {enrichment.post_fit_score}/10\n"
        f"Категории: {_escape_bounded(categories or Category.OTHER.value, 160)}\n\n"
        f"{_escape_bounded(enrichment.summary_ru, 1200)}\n\n"
        f"<b>Почему важно:</b> {_escape_bounded(enrichment.why_important, 600)}\n"
        f"<i>{_escape_bounded(reasons, 400)}</i>"
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
                f"- {card.enrichment.title_ru} [{card.material.score:.1f}]\n"
                f"  {card.enrichment.summary_ru}\n"
                f"  {card.material.url}"
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
    if Category.OPEN_SOURCE in categories:
        return "🚀 Open Source"
    if card.material.independent_mentions >= 2 or popularity >= 1000:
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
