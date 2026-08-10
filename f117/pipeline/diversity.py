from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from f117.domain import Category, RankedMaterial

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
    close_score_gap: float = 8.0
    discovery_selection_boost: float = 0.0
    other_discovery_boost_factor: float = 0.20
    editorial_fit_weight: float = 0.0
    culture_target_share: float = 0.50
    culture_close_score_gap: float = 8.0


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
    for position, material in enumerate(materials):
        entity = entity_key(material)
        category = material.categories[0].value if material.categories else "other"
        exceeds = (
            sources[material.source_name] >= config.max_per_source
            or (entity is not None and entities[entity] >= config.max_per_entity)
            or categories[category] >= config.max_per_category
        )
        material_score = _selection_score(material, config)
        if exceeds and material_score < config.strong_score_threshold:
            alternatives = materials[position + 1 :]
            has_close_alternative = any(
                _can_fit(alternative, sources, entities, categories, config)
                and _selection_score(alternative, config) >= material_score - config.close_score_gap
                for alternative in alternatives
            )
            has_deferred_peer = any(_same_cap_bucket(material, earlier) for earlier in deferred)
            if has_close_alternative or has_deferred_peer:
                deferred.append(material)
                continue
        accepted.append(material)
        sources[material.source_name] += 1
        if entity is not None:
            entities[entity] += 1
        categories[category] += 1
    # Preserve source-score order for all soft-cap overflow. Selection slices
    # this sequence, so a strong deferred candidate cannot be inverted by a
    # later weaker overflow item.
    return accepted + deferred


def soft_balance_cyberculture(
    materials: Sequence[RankedMaterial],
    *,
    top_n: int,
    config: DiversityConfig,
) -> list[RankedMaterial]:
    """Prefer comparable cyberculture cards without turning balance into a quota.

    The function only swaps in a culture candidate when it is close to the
    weakest currently selected non-culture candidate.  It never evicts a clear
    quality winner, and it never limits an already culture-heavy digest.
    """

    if top_n <= 1 or not materials:
        return list(materials)
    selected = list(materials[:top_n])
    remaining = list(materials[top_n:])
    target = round(top_n * config.culture_target_share)

    while _culture_count(selected) < target:
        culture_candidate = next(
            (material for material in remaining if _is_cyberculture(material)), None
        )
        replaceable = [material for material in selected if not _is_cyberculture(material)]
        if culture_candidate is None or not replaceable:
            break
        weakest = min(replaceable, key=lambda material: _selection_score(material, config))
        if _selection_score(culture_candidate, config) < (
            _selection_score(weakest, config) - config.culture_close_score_gap
        ):
            break
        selected.remove(weakest)
        remaining.remove(culture_candidate)
        remaining.append(weakest)
        selected.append(culture_candidate)

    positions = {material.material_id: index for index, material in enumerate(materials)}
    return sorted(
        [*selected, *remaining],
        key=lambda material: (
            0 if material in selected else 1,
            -_selection_score(material, config),
            positions[material.material_id],
        ),
    )


def _is_cyberculture(material: RankedMaterial) -> bool:
    return Category.CYBERCULTURE in material.categories


def _culture_count(materials: Sequence[RankedMaterial]) -> int:
    return sum(_is_cyberculture(material) for material in materials)


def _selection_score(material: RankedMaterial, config: DiversityConfig) -> float:
    base = material.score
    if config.editorial_fit_weight:
        base = (
            material.score * (1.0 - config.editorial_fit_weight)
            + material.editorial_fit * config.editorial_fit_weight
        )
    boost = config.discovery_selection_boost
    if not material.categories or set(material.categories) == {Category.OTHER}:
        boost *= config.other_discovery_boost_factor
    return base + material.discovery_score * boost


def _same_cap_bucket(a: RankedMaterial, b: RankedMaterial) -> bool:
    a_entity = entity_key(a)
    b_entity = entity_key(b)
    a_category = a.categories[0].value if a.categories else "other"
    b_category = b.categories[0].value if b.categories else "other"
    return (
        a.source_name == b.source_name
        or (a_entity is not None and a_entity == b_entity)
        or a_category == b_category
    )


def _can_fit(
    material: RankedMaterial,
    sources: Counter[str],
    entities: Counter[str],
    categories: Counter[str],
    config: DiversityConfig,
) -> bool:
    entity = entity_key(material)
    category = material.categories[0].value if material.categories else "other"
    return (
        sources[material.source_name] < config.max_per_source
        and (entity is None or entities[entity] < config.max_per_entity)
        and categories[category] < config.max_per_category
    )


def entity_key(material: RankedMaterial) -> str | None:
    text = f"{material.title} {material.description}"
    for entity in _KNOWN_ENTITIES:
        if re.search(rf"\b{re.escape(entity)}\b", text, re.IGNORECASE):
            return entity.casefold()
    return None
