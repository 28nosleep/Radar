from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from f117.domain import Category, FeedbackType, NormalizedItem
from f117.storage.models import FeedbackModel, MaterialModel, SourceModel
from f117.storage.repository import Repository


class _FailingInsertSession:
    def __init__(self, source: SourceModel) -> None:
        self.source = source
        self.added: list[object] = []
        self.commit_called = False
        self.rollback_called = False
        self.update_called = False

    async def get(self, _: object, __: object) -> SourceModel:
        return self.source

    async def scalar(self, _: object) -> None:
        return None

    async def execute(self, _: object) -> SimpleNamespace:
        self.update_called = True
        return SimpleNamespace(rowcount=1)

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        raise IntegrityError("INSERT", {}, RuntimeError("unique constraint"))

    async def commit(self) -> None:
        self.commit_called = True

    async def rollback(self) -> None:
        self.rollback_called = True


class _DatabaseWithFailingInsert:
    def __init__(self, session: _FailingInsertSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self) -> AsyncIterator[_FailingInsertSession]:
        yield self._session


@pytest.mark.asyncio
async def test_duplicate_insert_and_mention_increment_roll_back_together() -> None:
    source_id = uuid4()
    session = _FailingInsertSession(
        SourceModel(
            id=source_id,
            key="source-b",
            name="Source B",
            feed_url="https://example.com/feed",
            reputation=0.8,
            enabled=True,
            default_categories=[],
        )
    )
    repository = Repository(cast(Any, _DatabaseWithFailingInsert(session)))
    item = NormalizedItem(
        external_id="duplicate-entry",
        source_key="source-b",
        source_name="Source B",
        source_reputation=0.8,
        title="A duplicated story",
        url="https://example.com/story",
        canonical_url="https://example.com/story",
        published_at=datetime.now(UTC),
        collected_at=datetime.now(UTC),
        categories=[Category.AI],
        content_hash="a" * 64,
        normalized_title="a duplicated story",
    )

    with pytest.raises(IntegrityError):
        await repository.add_material(source_id, item, duplicate_of_id=uuid4())

    assert session.update_called is True
    assert session.added
    assert session.commit_called is False
    assert session.rollback_called is True


class _FeedbackSession:
    def __init__(self, material: MaterialModel, feedback: FeedbackModel | None = None) -> None:
        self.material = material
        self.feedback = feedback
        self.added: list[object] = []
        self.commits = 0

    async def get(self, *_: object) -> None:
        return None

    async def scalar(self, _: object) -> MaterialModel | FeedbackModel | None:
        if not hasattr(self, "_material_returned"):
            self._material_returned = True
            return self.material
        return self.feedback

    def add(self, value: object) -> None:
        self.added.append(value)
        if isinstance(value, FeedbackModel):
            self.feedback = value

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class _FeedbackDatabase:
    def __init__(self, sessions: list[_FeedbackSession]) -> None:
        self.sessions = sessions

    @asynccontextmanager
    async def session(self) -> AsyncIterator[_FeedbackSession]:
        yield self.sessions.pop(0)


@pytest.mark.asyncio
async def test_feedback_is_created_then_updated_for_the_same_material() -> None:
    source = SourceModel(
        id=uuid4(),
        key="github",
        name="GitHub",
        feed_url="https://api.github.com",
        reputation=0.8,
        enabled=True,
        default_categories=[],
    )
    material = MaterialModel(
        id=uuid4(),
        source=source,
        external_id="project",
        title="Project",
        url="https://example.com/project",
        canonical_url="https://example.com/project",
        published_at=datetime.now(UTC),
        collected_at=datetime.now(UTC),
        description="",
        categories=["open_source"],
        popularity={},
        content_hash="b" * 64,
        normalized_title="project",
        score=77.0,
        discovery_score=61.0,
    )
    first = _FeedbackSession(material)
    repository = Repository(cast(Any, _FeedbackDatabase([first])))

    created = await repository.record_feedback(
        material_id=material.id,
        feedback_type=FeedbackType.USEFUL,
        telegram_update_id=1,
    )

    assert created is not None
    assert first.feedback is not None
    assert first.feedback.feedback_type == "useful"
    assert first.feedback.source_key == "github"

    second = _FeedbackSession(material, first.feedback)
    repository = Repository(cast(Any, _FeedbackDatabase([second])))
    updated = await repository.record_feedback(
        material_id=material.id,
        feedback_type=FeedbackType.POST,
        telegram_update_id=2,
    )

    assert updated is not None
    assert second.feedback is first.feedback
    assert second.feedback.feedback_type == "post"
    assert len(second.added) == 1  # only idempotency record; no second feedback row
