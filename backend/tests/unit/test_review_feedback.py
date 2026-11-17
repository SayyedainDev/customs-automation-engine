"""Tests for the feedback-review query logic (docs section 14.4). Only the
query functions (``fetch_feedback``/``_preceding_question``) are tested here
with an in-memory database - the script's ``main()`` needs a real
``CUSTOMS_DATABASE_URL`` and is exercised manually, not in CI."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.customs_chat import ChatConversation, ChatTurn
from scripts.review_feedback import fetch_feedback, _preceding_question


@pytest.fixture()
def engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def _seed_turn(db: Session, *, conversation_id, role, content, created_at, feedback=None):
    db.add(
        ChatTurn(
            id=uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            user_feedback=feedback,
            created_at=created_at,
        )
    )


def test_fetch_feedback_filters_by_rating(engine):
    with Session(engine) as db:
        conv_id = uuid4()
        db.add(ChatConversation(id=conv_id))
        now = datetime.now(timezone.utc)
        _seed_turn(db, conversation_id=conv_id, role="user", content="What is a bill of lading?", created_at=now)
        _seed_turn(
            db,
            conversation_id=conv_id,
            role="assistant",
            content="A bill of lading is...",
            created_at=now + timedelta(seconds=1),
            feedback="down",
        )
        _seed_turn(
            db,
            conversation_id=conv_id,
            role="assistant",
            content="Something else",
            created_at=now + timedelta(seconds=2),
            feedback="up",
        )
        db.commit()

        down_turns = fetch_feedback(db, rating="down", since=None, limit=10)
        assert len(down_turns) == 1
        assert down_turns[0].content == "A bill of lading is..."

        up_turns = fetch_feedback(db, rating="up", since=None, limit=10)
        assert len(up_turns) == 1


def test_fetch_feedback_respects_since(engine):
    with Session(engine) as db:
        conv_id = uuid4()
        db.add(ChatConversation(id=conv_id))
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recent = datetime(2026, 8, 1, tzinfo=timezone.utc)
        _seed_turn(db, conversation_id=conv_id, role="assistant", content="old miss", created_at=old, feedback="down")
        _seed_turn(
            db, conversation_id=conv_id, role="assistant", content="recent miss", created_at=recent, feedback="down"
        )
        db.commit()

        recent_only = fetch_feedback(db, rating="down", since=datetime(2026, 6, 1, tzinfo=timezone.utc), limit=10)
        assert [t.content for t in recent_only] == ["recent miss"]


def test_preceding_question_finds_the_matching_user_turn(engine):
    with Session(engine) as db:
        conv_id = uuid4()
        db.add(ChatConversation(id=conv_id))
        base = datetime.now(timezone.utc)
        _seed_turn(db, conversation_id=conv_id, role="user", content="What is Form-E?", created_at=base)
        answer_time = base + timedelta(seconds=1)
        _seed_turn(
            db,
            conversation_id=conv_id,
            role="assistant",
            content="Form-E is...",
            created_at=answer_time,
            feedback="down",
        )
        db.commit()

        answer = db.query(ChatTurn).filter_by(content="Form-E is...").one()
        assert _preceding_question(db, answer) == "What is Form-E?"


def test_preceding_question_returns_none_with_no_prior_user_turn(engine):
    with Session(engine) as db:
        conv_id = uuid4()
        db.add(ChatConversation(id=conv_id))
        _seed_turn(
            db,
            conversation_id=conv_id,
            role="assistant",
            content="orphaned answer",
            created_at=datetime.now(timezone.utc),
            feedback="down",
        )
        db.commit()

        answer = db.query(ChatTurn).filter_by(content="orphaned answer").one()
        assert _preceding_question(db, answer) is None
