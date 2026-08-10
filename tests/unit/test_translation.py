from __future__ import annotations

from collections.abc import Sequence

import pytest

from f117.adapters.translation import LocalTranslationProvider, TranslatingEditorialEnricher
from f117.domain import EditorialCard, EditorialEnrichment, RankedMaterial
from tests.unit.test_openai_editorial import _material


class _Cache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def cached_translation(self, cache_key: str) -> str | None:
        return self.values.get(cache_key)

    async def store_translation(self, *, cache_key: str, translated_text: str, **_: str) -> None:
        self.values[cache_key] = translated_text


@pytest.mark.asyncio
async def test_local_translation_preserves_names_numbers_urls_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, str]] = []

    class Response:
        status = 200

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, **_: object) -> dict[str, str]:
            return {"translatedText": "[RADAR0] выпустила [RADAR1] с [RADAR2]%: [RADAR3]"}

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, _: str, *, json: dict[str, str]) -> Response:
            requests.append(json)
            return Response()

    monkeypatch.setattr("f117.adapters.translation.aiohttp.ClientSession", Session)
    provider = LocalTranslationProvider(
        base_url="http://translator:5000",
        cache=_Cache(),
        timeout_seconds=2,
        max_input_chars=500,
    )
    source = "OpenAI released GPT-5 with 25%: https://example.com/a"

    first = await provider.translate(source)
    second = await provider.translate(source)

    assert first == "OpenAI выпустила GPT-5 с 25%: https://example.com/a"
    assert second == first
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_local_translation_gracefully_falls_back_to_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 503

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    class Session:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def post(self, *_: object, **__: object) -> Response:
            return Response()

    monkeypatch.setattr("f117.adapters.translation.aiohttp.ClientSession", Session)
    provider = LocalTranslationProvider(
        base_url="http://translator:5000",
        cache=_Cache(),
        timeout_seconds=2,
        max_input_chars=500,
    )

    assert await provider.translate("Anthropic released a model.") == "Anthropic released a model."


@pytest.mark.asyncio
async def test_translation_wrapper_uses_local_provider_before_editorial_reasoning() -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def translate(self, text: str, **_: str) -> str:
            self.calls.append(text)
            return f"RU:{text}"

    class Editorial:
        def __init__(self) -> None:
            self.received: list[RankedMaterial] = []

        async def enrich(self, materials: Sequence[RankedMaterial]) -> list[EditorialCard]:
            self.received.extend(materials)
            return [
                EditorialCard(
                    material=material,
                    enrichment=EditorialEnrichment(
                        title_ru=material.translated_title_ru or "",
                        summary_ru=material.translated_summary_ru or "",
                        post_fit_score=5,
                    ),
                )
                for material in materials
            ]

    provider = Provider()
    editorial = Editorial()
    await TranslatingEditorialEnricher(provider, editorial).enrich([_material()])

    assert provider.calls == ["A new robot", "A short factual description."]
    assert editorial.received[0].translated_title_ru == "RU:A new robot"
    assert editorial.received[0].translated_summary_ru.startswith("RU:")
