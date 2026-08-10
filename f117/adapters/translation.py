from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from functools import partial
from typing import Protocol

import aiohttp

from f117.adapters.openai_editorial import EditorialEnricher
from f117.domain import EditorialCard, RankedMaterial

logger = logging.getLogger(__name__)


class TranslationCache(Protocol):
    async def cached_translation(self, cache_key: str) -> str | None: ...

    async def store_translation(
        self,
        *,
        cache_key: str,
        source_language: str,
        target_language: str,
        source_text_hash: str,
        translated_text: str,
    ) -> None: ...


class TranslationProvider(Protocol):
    async def translate(self, text: str, *, source: str = "en", target: str = "ru") -> str: ...


class LocalTranslationProvider:
    """Bounded LibreTranslate client with durable cache and zero-cost fallback."""

    def __init__(
        self,
        *,
        base_url: str,
        cache: TranslationCache,
        timeout_seconds: float,
        max_input_chars: int,
        enabled: bool = True,
        max_concurrency: int = 1,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/translate"
        self.cache = cache
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_input_chars = max_input_chars
        self.enabled = enabled
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def translate(self, text: str, *, source: str = "en", target: str = "ru") -> str:
        original = text.strip()
        if not original or source == target or not self.enabled:
            return original
        bounded = _bounded_complete_text(original, self.max_input_chars)
        source_hash = hashlib.sha256(bounded.encode()).hexdigest()
        cache_key = hashlib.sha256(f"{source}\0{target}\0{source_hash}".encode()).hexdigest()
        if cached := await self.cache.cached_translation(cache_key):
            return cached

        protected, tokens = _protect_tokens(bounded)
        try:
            async with self.semaphore:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(
                        self.url,
                        json={
                            "q": protected,
                            "source": source,
                            "target": target,
                            "format": "text",
                        },
                    ) as response:
                        if response.status != 200:
                            raise RuntimeError(f"local translator returned HTTP {response.status}")
                        payload = await response.json(content_type=None)
            translated = payload.get("translatedText") if isinstance(payload, dict) else None
            if not isinstance(translated, str) or not translated.strip():
                raise RuntimeError("local translator returned no translatedText")
            restored = _restore_tokens(translated.strip(), tokens)
        except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Local EN->RU translation unavailable; using English: %s: %s",
                type(exc).__name__,
                exc,
            )
            return bounded

        await self.cache.store_translation(
            cache_key=cache_key,
            source_language=source,
            target_language=target,
            source_text_hash=source_hash,
            translated_text=restored,
        )
        return restored


class TranslatingEditorialEnricher:
    """Translate only final deterministic/manual candidates before editorial reasoning."""

    def __init__(self, provider: TranslationProvider, editorial: EditorialEnricher) -> None:
        self.provider = provider
        self.editorial = editorial

    async def enrich(self, materials: Sequence[RankedMaterial]) -> list[EditorialCard]:
        translated = await asyncio.gather(*(self._translate(material) for material in materials))
        return await self.editorial.enrich(translated)

    async def _translate(self, material: RankedMaterial) -> RankedMaterial:
        excerpt = material.description.strip() or material.title
        title_ru, summary_ru = await asyncio.gather(
            self.provider.translate(material.title),
            self.provider.translate(excerpt),
        )
        return material.model_copy(
            update={"translated_title_ru": title_ru, "translated_summary_ru": summary_ru}
        )


_PROTECTED_RE = re.compile(
    r"https?://\S+|\b(?:OpenAI|Anthropic|NVIDIA|Figure AI|Boston Dynamics|Claude|Atlas|BMW|"
    r"API|GPU|GPT(?:-\d+(?:\.\d+)?)?|xAI|GitHub|arXiv)\b|"
    r"\b\d+(?:[.,]\d+)*(?:%|[A-Za-z]+)?\b",
    re.IGNORECASE,
)


def _protect_tokens(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        suffix = ""
        if token.casefold().startswith(("http://", "https://")):
            clean_token = token.rstrip(".,;:!?")
            suffix = token[len(clean_token) :]
            token = clean_token
        tokens.append(token)
        return f"[RADAR{len(tokens) - 1}]{suffix}"

    return _PROTECTED_RE.sub(replace, text), tokens


def _restore_tokens(text: str, tokens: list[str]) -> str:
    restored = text
    for index, token in enumerate(tokens):
        placeholder = re.compile(rf"\[?_*RADAR\s*{index}_*\]?", re.IGNORECASE)
        if placeholder.search(restored) is None:
            raise ValueError("local translator changed a protected name, number, or URL")
        restored = placeholder.sub(partial(_literal_replacement, value=token), restored, count=1)
    restored = re.sub(r"\s+", " ", restored).strip()
    return re.sub(r"\s+([.,!?;:%])", r"\1", restored)


def _literal_replacement(_: re.Match[str], *, value: str) -> str:
    return value


def _bounded_complete_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    prefix = text[:limit]
    complete_at = max(prefix.rfind(". "), prefix.rfind("? "), prefix.rfind("! "))
    return prefix[: complete_at + 1] if complete_at >= limit // 2 else prefix.rstrip()
