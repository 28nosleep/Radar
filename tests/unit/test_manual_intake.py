from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from f117.adapters.manual_url import ManualPage, SafeManualURLFetcher, UnsafeManualURL
from f117.config import Settings
from f117.domain import AIVerdict, EditorialCard, EditorialEnrichment, FeedSource, StoredMaterial
from f117.pipeline.editorial import EditorialConfig
from f117.pipeline.ranking import RankingConfig
from f117.services.manual_intake import ManualIntakeService
from f117.storage.repository import SourceState


class _Fetcher:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str) -> ManualPage:
        self.calls += 1
        return ManualPage(
            requested_url=url,
            final_url=url,
            title="Anthropic announces a model",
            description="The company claims better coding results.",
            source_name="Anthropic",
            published_at=datetime.now(UTC),
        )


class _Repository:
    def __init__(self) -> None:
        self.material: StoredMaterial | None = None
        self.source_id = uuid4()
        self.added = 0

    async def material_by_canonical_url(self, canonical_url: str) -> StoredMaterial | None:
        if self.material is not None and self.material.item.canonical_url == canonical_url:
            return self.material
        return None

    async def ensure_source(self, source: FeedSource) -> SourceState:
        return SourceState(self.source_id, source, None, None)

    async def add_material(self, source_id: UUID, item: object) -> StoredMaterial:
        assert source_id == self.source_id
        self.added += 1
        self.material = StoredMaterial(id=uuid4(), item=item)  # type: ignore[arg-type]
        return self.material

    async def mark_manual_submission(
        self, material_id: UUID, *, content_insufficient: bool = False
    ) -> StoredMaterial:
        del content_insufficient
        assert self.material is not None and self.material.id == material_id
        self.material = self.material.model_copy(
            update={
                "item": self.material.item.model_copy(
                    update={"qualitative_signals": ["manual_submission"]}
                )
            }
        )
        return self.material

    async def save_ranking(self, _: object) -> None:
        return None

    async def save_enrichment(self, *_: object, **__: object) -> None:
        return None


class _Enricher:
    async def enrich(self, materials: list[object]) -> list[EditorialCard]:
        material = materials[0]
        return [
            EditorialCard(
                material=material,  # type: ignore[arg-type]
                enrichment=EditorialEnrichment(
                    title_ru="Anthropic анонсировала модель",
                    summary_ru="Компания заявляет об улучшении результатов в программировании.",
                    ai_opinion=(
                        "Это заявление самой компании без независимой проверки и данных о широком "
                        "внедрении. Заголовок выглядит заметнее доступной фактуры, поэтому считать "
                        "релиз сильным сигналом пока рано. Данных пока недостаточно, чтобы считать "
                        "это значимым, а маркетинговую формулировку — подтверждённым результатом."
                    ),
                    ai_verdict=AIVerdict.SKIP,
                    post_fit_score=2,
                ),
            )
        ]


async def _allow_public_url(_: str) -> None:
    return None


@pytest.mark.asyncio
async def test_manual_url_ingestion_deduplicates_and_keeps_skip_result() -> None:
    repository = _Repository()
    fetcher = _Fetcher()
    service = ManualIntakeService(
        settings=Settings(_env_file=None),
        repository=repository,  # type: ignore[arg-type]
        fetcher=fetcher,  # type: ignore[arg-type]
        enricher=_Enricher(),  # type: ignore[arg-type]
        ranking_config=RankingConfig(),
        editorial_config=EditorialConfig(),
        url_validator=_allow_public_url,
    )

    first = await service.process("https://anthropic.com/news/model?utm_source=share")
    second = await service.process("https://anthropic.com/news/model")

    assert first.duplicate is False
    assert second.duplicate is True
    assert repository.added == 1
    assert fetcher.calls == 1
    assert second.card.enrichment.ai_verdict == AIVerdict.SKIP
    assert second.card.material.manual_submission is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/admin", "http://[::1]/", "http://10.0.0.1/", "file:///etc/passwd"],
)
async def test_manual_fetch_rejects_invalid_or_private_urls(url: str) -> None:
    fetcher = SafeManualURLFetcher(
        timeout_seconds=1,
        redirect_limit=2,
        max_response_bytes=10_000,
        user_agent="test",
    )

    with pytest.raises(UnsafeManualURL):
        await fetcher.fetch(url)
