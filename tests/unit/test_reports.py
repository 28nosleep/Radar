from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from f117.services.reports import discovery_report, quality_report


class _ReportRepository:
    def __init__(self) -> None:
        source = SimpleNamespace(key="github", name="GitHub")
        self.materials = [
            SimpleNamespace(
                id=uuid4(),
                title="Fast project",
                source=source,
                selected_at=datetime.now(UTC),
                score=80.0,
                discovery_score=65.0,
                popularity={"github_stars": 100.0, "growth_per_hour": 40.0},
            ),
            SimpleNamespace(
                id=uuid4(),
                title="Quiet project",
                source=source,
                selected_at=None,
                score=40.0,
                discovery_score=10.0,
                popularity={"github_stars": 20.0},
            ),
        ]
        self.feedback = [
            SimpleNamespace(source_key="github", feedback_type="useful"),
            SimpleNamespace(source_key="github", feedback_type="post"),
        ]
        self.deliveries = [SimpleNamespace(material_id=self.materials[0].id)]

    async def report_materials(self, *, days: int) -> list[SimpleNamespace]:
        assert days == 7
        return self.materials

    async def report_feedback(self, *, days: int) -> list[SimpleNamespace]:
        assert days == 7
        return self.feedback

    async def report_deliveries(self, *, days: int) -> list[SimpleNamespace]:
        assert days == 7
        return self.deliveries


@pytest.mark.asyncio
async def test_quality_report_summarizes_source_feedback() -> None:
    report = await quality_report(_ReportRepository(), days=7)  # type: ignore[arg-type]

    assert report["sources"] == [
        {
            "source": "github",
            "name": "GitHub",
            "collected": 2,
            "top": 1,
            "sent": 1,
            "useful": 1,
            "miss": 0,
            "post": 1,
            "average_importance_score": 60.0,
            "average_discovery_score": 37.5,
        }
    ]
    assert report["delivered"] == 1
    assert report["useful"] == 1
    assert report["missed"] == 0
    assert report["saved"] == 1
    assert report["useful_rate"] == 1.0
    assert report["save_rate"] == 1.0
    assert report["sources_with_most_saves"] == [{"source": "github", "post": 1}]


@pytest.mark.asyncio
async def test_discovery_report_is_honest_when_history_is_small() -> None:
    report = await discovery_report(
        _ReportRepository(),  # type: ignore[arg-type]
        days=7,
        rising_threshold=55,
        hidden_gem_max_popularity=1000,
    )

    assert report["note"] is not None
    assert report["discovery_score"]["count"] == 2
    assert [item["title"] for item in report["rising_candidates"]] == ["Fast project"]
