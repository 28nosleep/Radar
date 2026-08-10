from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from f117.adapters.manual_url import SafeManualURLFetcher, validate_public_url
from f117.adapters.openai_editorial import EditorialEnricher
from f117.config import Settings
from f117.domain import CollectedItem, EditorialCard, FeedSource, StoredMaterial
from f117.pipeline.classifier import classify_item
from f117.pipeline.editorial import EditorialConfig, assess_editorial_fit
from f117.pipeline.normalizer import normalize_item, normalize_url
from f117.pipeline.ranking import RankingConfig, score_material
from f117.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class ManualIntakeResult:
    card: EditorialCard
    duplicate: bool
    fetch_error: str | None


class ManualIntakeService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: Repository,
        fetcher: SafeManualURLFetcher,
        enricher: EditorialEnricher,
        ranking_config: RankingConfig,
        editorial_config: EditorialConfig,
        url_validator: Callable[[str], Awaitable[None]] = validate_public_url,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.fetcher = fetcher
        self.enricher = enricher
        self.ranking_config = ranking_config
        self.editorial_config = editorial_config
        self.url_validator = url_validator

    async def process(self, url: str) -> ManualIntakeResult:
        canonical_requested = normalize_url(url)
        await self.url_validator(canonical_requested)
        existing = await self.repository.material_by_canonical_url(canonical_requested)
        duplicate = existing is not None
        if existing is not None:
            stored = await self.repository.mark_manual_submission(
                existing.id, content_insufficient=False
            )
            fetch_error = None
        else:
            page = await self.fetcher.fetch(canonical_requested)
            canonical = normalize_url(page.final_url)
            existing = await self.repository.material_by_canonical_url(canonical)
            duplicate = existing is not None
            if existing is not None:
                stored = await self.repository.mark_manual_submission(
                    existing.id, content_insufficient=page.fetch_error is not None
                )
                fetch_error = page.fetch_error
                return await self._evaluate(stored, duplicate=duplicate, fetch_error=fetch_error)
            parsed = urlsplit(canonical)
            source_key = _manual_source_key(parsed.hostname or "unknown")
            source = FeedSource(
                key=source_key,
                name=page.source_name,
                feed_url=f"{parsed.scheme}://{parsed.netloc}/",
                site_url=f"{parsed.scheme}://{parsed.netloc}/",
                reputation=0.5,
            )
            state = await self.repository.ensure_source(source)
            signals = ["manual_submission"]
            if page.fetch_error:
                signals.append("manual_content_insufficient")
            if page.published_at is None:
                signals.append("published_at_unknown")
            collected = CollectedItem(
                external_id=hashlib.sha256(canonical.encode()).hexdigest(),
                source_key=source_key,
                source_name=page.source_name,
                source_reputation=0.5,
                title=page.title,
                url=canonical,
                published_at=page.published_at,
                collected_at=datetime.now(UTC),
                description=page.description,
                author=page.author,
                qualitative_signals=signals,
                raw={"manual_requested_url": canonical_requested, "fetch_error": page.fetch_error},
            )
            stored = await self.repository.add_material(
                state.id, classify_item(normalize_item(collected))
            )

            fetch_error = page.fetch_error

        return await self._evaluate(stored, duplicate=duplicate, fetch_error=fetch_error)

    async def _evaluate(
        self, stored: StoredMaterial, *, duplicate: bool, fetch_error: str | None
    ) -> ManualIntakeResult:

        ranked = score_material(stored, config=self.ranking_config)
        assessed = assess_editorial_fit(stored, ranked, config=self.editorial_config)
        manual_fit = min(100.0, assessed.fit + 8.0)
        manual_delivery = min(100.0, assessed.delivery_score + 5.0)
        ranked = ranked.model_copy(
            update={
                "editorial_fit": manual_fit,
                "editorial_reasons": [
                    *assessed.reasons,
                    "manual submission: modest owner-interest boost, not proof of quality",
                ],
                "delivery_score": manual_delivery,
                "manual_submission": True,
            }
        )
        await self.repository.save_ranking(ranked)
        card = (await self.enricher.enrich([ranked]))[0]
        if card.llm_model is not None and card.editorial_error is None:
            await self.repository.save_enrichment(
                ranked.material_id,
                card.enrichment,
                model=card.llm_model,
                usage=card.usage,
            )
        return ManualIntakeResult(card=card, duplicate=duplicate, fetch_error=fetch_error)


def _manual_source_key(hostname: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", hostname.casefold()).strip("-")[:80]
    digest = hashlib.sha256(hostname.encode()).hexdigest()[:8]
    return f"manual-{readable or 'source'}-{digest}"
