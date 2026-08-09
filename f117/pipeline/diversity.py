from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from f117.domain import RankedMaterial

_KNOWN_ENTITIES = (
    "OpenAI",
    "Anthropic",
    "Google DeepMind",
    "DeepMind",
    "Google",
    "Meta",
    "Microsoft",
    "NVIDIA",
    "Tesla",
    "Figure",
    "Boston Dynamics",
    "Unitree",
    "Neuralink",
    "Apple",
)


@dataclass(frozen=True, slots=True)
class DiversityConfig:
    max_per_source: int = 2
    max_per_entity: int = 2
    max_per_category: int = 4
    strong_score_threshold: float = 85.0


def diversify(
    materials: Sequence[RankedMaterial], *, config: DiversityConfig
) -> list[RankedMaterial]:
    """Prefer varied worthy cards, without throwing away strong or lone candidates.

    Deferred candidates are returned once no alternative remains. This makes every
    cap soft and preserves an important material even in a narrow news cycle.
    """

    accepted: list[RankedMaterial] = []
    deferred: list[RankedMaterial] = []
    sources: Counter[str] = Counter()
    entities: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    for material in materials:
        entity = entity_key(material)
        category = material.categories[0].value if material.categories else "other"
        exceeds = (
            sources[material.source_name] >= config.max_per_source
            or (entity is not None and entities[entity] >= config.max_per_entity)
            or categories[category] >= config.max_per_category
        )
        if exceeds and material.score < config.strong_score_threshold:
            deferred.append(material)
            continue
        accepted.append(material)
        sources[material.source_name] += 1
        if entity is not None:
            entities[entity] += 1
        categories[category] += 1
    return accepted + deferred


def entity_key(material: RankedMaterial) -> str | None:
    text = f"{material.title} {material.description}"
    for entity in _KNOWN_ENTITIES:
        if re.search(rf"\b{re.escape(entity)}\b", text, re.IGNORECASE):
            return entity.casefold()
    return None
