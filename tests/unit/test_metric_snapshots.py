from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from f117.storage.repository import _with_growth


def test_growth_is_calculated_from_two_metric_snapshots() -> None:
    captured_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    previous = SimpleNamespace(
        captured_at=captured_at - timedelta(hours=4), metrics={"github_stars": 120.0}
    )

    result = _with_growth({"github_stars": 420.0}, previous, captured_at)  # type: ignore[arg-type]

    assert result["growth_absolute"] == 300.0
    assert result["growth_percent"] == 250.0
    assert result["growth_per_hour"] == 62.5
    assert result["growth_window_hours"] == 4.0


def test_first_snapshot_has_no_growth() -> None:
    assert _with_growth({"youtube_views": 5000.0}, None, datetime.now(UTC)) == {
        "youtube_views": 5000.0
    }
