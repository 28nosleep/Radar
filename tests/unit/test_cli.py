from __future__ import annotations

from uuid import uuid4

import pytest

from f117.cli import main as cli
from f117.config import Settings
from f117.services.digest import RunSummary


class _Database:
    disposed = False

    def __init__(self, _: str) -> None:
        pass

    async def dispose(self) -> None:
        type(self).disposed = True


class _Repository:
    def __init__(self, _: _Database) -> None:
        pass


@pytest.mark.asyncio
async def test_scheduler_start_waits_before_any_digest_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StoppingEvent:
        def __init__(self) -> None:
            self.stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def set(self) -> None:
            self.stopped = True

        async def wait(self) -> None:
            self.stopped = True

    class Loop:
        def add_signal_handler(self, *_: object) -> None:
            pass

    class Service:
        async def run_once(self) -> RunSummary:
            raise AssertionError("scheduler must not run a digest on startup")

    _Database.disposed = False
    monkeypatch.setattr(cli, "Database", _Database)
    monkeypatch.setattr(cli, "Repository", _Repository)
    monkeypatch.setattr(cli, "build_digest_service", lambda *_: Service())
    monkeypatch.setattr(cli, "build_feedback_poller", lambda *_: None)
    monkeypatch.setattr(cli.asyncio, "Event", StoppingEvent)
    monkeypatch.setattr(cli.asyncio, "get_running_loop", Loop)

    await cli._run_scheduler(Settings(_env_file=None))

    assert _Database.disposed


@pytest.mark.asyncio
async def test_explicit_run_once_still_invokes_digest_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Service:
        calls = 0

        async def run_once(self) -> RunSummary:
            type(self).calls += 1
            return RunSummary(
                run_id=uuid4(),
                status="completed",
                collected_count=0,
                inserted_count=0,
                duplicate_count=0,
                candidate_count=0,
                selected_count=0,
                delivered_count=0,
                editorial_failure_count=0,
            )

    _Database.disposed = False
    monkeypatch.setattr(cli, "Database", _Database)
    monkeypatch.setattr(cli, "Repository", _Repository)
    monkeypatch.setattr(cli, "build_digest_service", lambda *_: Service())

    await cli._run_once(Settings(_env_file=None))

    assert Service.calls == 1
    assert _Database.disposed
