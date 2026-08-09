from __future__ import annotations

from statistics import mean
from typing import Any
from uuid import UUID

from f117.domain import FeedbackType
from f117.storage.repository import Repository


async def quality_report(repository: Repository, *, days: int) -> dict[str, Any]:
    """Return a compact, auditable source-quality snapshot for the requested window."""

    materials = await repository.report_materials(days=days)
    feedback = await repository.report_feedback(days=days)
    deliveries = await repository.report_deliveries(days=days)
    by_source: dict[str, dict[str, Any]] = {}
    material_source: dict[UUID, str] = {}
    for material in materials:
        source = material.source.key
        material_source[material.id] = source
        row = by_source.setdefault(source, _quality_row(material.source.name))
        row["collected"] += 1
        row["top"] += int(material.selected_at is not None)
        row["importance_scores"].append(material.score)
        row["discovery_scores"].append(material.discovery_score)

    for delivery in deliveries:
        delivery_source = material_source.get(delivery.material_id)
        if delivery_source is not None:
            by_source[delivery_source]["sent"] += 1

    for item in feedback:
        row = by_source.setdefault(item.source_key, _quality_row(item.source_key))
        if item.feedback_type == FeedbackType.USEFUL.value:
            row["useful"] += 1
        elif item.feedback_type == FeedbackType.MISS.value:
            row["miss"] += 1
        elif item.feedback_type == FeedbackType.POST.value:
            row["post"] += 1

    sources: list[dict[str, Any]] = []
    for key in sorted(by_source):
        row = by_source[key]
        importance = row.pop("importance_scores")
        discovery = row.pop("discovery_scores")
        sources.append(
            {
                "source": key,
                **row,
                "average_importance_score": _rounded_mean(importance),
                "average_discovery_score": _rounded_mean(discovery),
            }
        )
    return {"days": days, "materials": len(materials), "sources": sources}


async def discovery_report(
    repository: Repository,
    *,
    days: int,
    rising_threshold: float,
    hidden_gem_max_popularity: float,
) -> dict[str, Any]:
    """Describe current rule-based discovery outputs without changing thresholds."""

    materials = await repository.report_materials(days=days)
    discovery_scores = [material.discovery_score for material in materials]
    importance_scores = [material.score for material in materials]
    growth = [
        float(material.popularity["growth_per_hour"])
        for material in materials
        if isinstance(material.popularity.get("growth_per_hour"), int | float)
    ]
    rising = [material for material in materials if material.discovery_score >= rising_threshold]
    hidden_gems = [
        material
        for material in rising
        if _popularity_total(material.popularity) <= hidden_gem_max_popularity
    ]
    return {
        "days": days,
        "materials": len(materials),
        "note": (
            "Too little data for calibration; collect several days of metric snapshots."
            if len(materials) < 10
            else None
        ),
        "importance_score": _distribution(importance_scores),
        "discovery_score": _distribution(discovery_scores),
        "growth_per_hour": _distribution(growth),
        "rising_candidates": _candidates(rising),
        "hidden_gem_candidates": _candidates(hidden_gems),
    }


def _quality_row(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "collected": 0,
        "top": 0,
        "sent": 0,
        "useful": 0,
        "miss": 0,
        "post": 0,
        "importance_scores": [],
        "discovery_scores": [],
    }


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "average": None, "min": None, "p50": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "average": round(mean(ordered), 2),
        "min": round(ordered[0], 2),
        "p50": round(_percentile(ordered, 0.5), 2),
        "p90": round(_percentile(ordered, 0.9), 2),
        "max": round(ordered[-1], 2),
    }


def _percentile(values: list[float], percentile: float) -> float:
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _popularity_total(popularity: dict[str, float]) -> float:
    return sum(
        value
        for key, value in popularity.items()
        if key
        not in {"growth_absolute", "growth_percent", "growth_per_hour", "growth_window_hours"}
        and not key.endswith(("_absolute", "_percent", "_per_hour"))
    )


def _candidates(materials: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(material.id),
            "title": material.title,
            "source": material.source.key,
            "importance_score": round(material.score, 2),
            "discovery_score": round(material.discovery_score, 2),
            "growth_per_hour": material.popularity.get("growth_per_hour"),
        }
        for material in sorted(materials, key=lambda item: item.discovery_score, reverse=True)[:20]
    ]
