"""Tests for pipeline/todo.py — deriving and syncing the to-do list.

Uses a temporary on-disk SQLite database (pytest's tmp_path) so this
exercises the real INSERT ... ON CONFLICT DO NOTHING / UPDATE paths, not a
mock.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus, ReplyOutlineStatus
from pipeline import persist, todo

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "todo.db"


def email(email_id="e1", **overrides) -> ProcessedEmail:
    defaults = dict(
        email_id=email_id, thread_id="t-" + email_id, sender="Dana <dana@example.com>",
        subject="Subject " + email_id, received_at=NOW, read_status=ReadStatus.READ,
        is_no_reply=False, importance_score=50.0, importance_level=ImportanceLevel.MEDIUM,
        reply_outline_status=ReplyOutlineStatus.SUGGESTED, processed_at=NOW,
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


class TestDeriveAndSync:
    def test_action_items_become_open_todos(self, db_path):
        persist.upsert([email(action_items=["Send the contract", "Confirm headcount"])], db_path)
        todo.sync(persist.all_processed(db_path), db_path)

        rows = todo.list_open(db_path)
        texts = {r["text"] for r in rows if r["kind"] == "action_item"}
        assert texts == {"Send the contract", "Confirm headcount"}

    def test_needs_reply_todo_for_unreplied_non_no_reply_email(self, db_path):
        persist.upsert([email(reply_outline_status=ReplyOutlineStatus.SUGGESTED)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)

        rows = todo.list_open(db_path)
        assert any(r["kind"] == "needs_reply" for r in rows)

    def test_no_reply_email_gets_no_needs_reply_todo(self, db_path):
        persist.upsert([email(is_no_reply=True, reply_outline_status=ReplyOutlineStatus.NOT_APPLICABLE)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)

        rows = todo.list_open(db_path)
        assert not any(r["kind"] == "needs_reply" for r in rows)

    def test_sent_reply_gets_no_needs_reply_todo(self, db_path):
        persist.upsert([email(reply_outline_status=ReplyOutlineStatus.SENT)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)

        rows = todo.list_open(db_path)
        assert not any(r["kind"] == "needs_reply" for r in rows)


class TestCompletionSurvivesResync:
    def test_completed_action_item_does_not_reappear(self, db_path):
        # is_no_reply=True keeps this email's needs_reply row from ever
        # existing, isolating the assertion to the action-item row alone.
        persist.upsert(
            [email(action_items=["Send the contract"], is_no_reply=True,
                   reply_outline_status=ReplyOutlineStatus.NOT_APPLICABLE)],
            db_path,
        )
        todo.sync(persist.all_processed(db_path), db_path)
        [row] = [r for r in todo.list_open(db_path) if r["kind"] == "action_item"]

        assert todo.complete(row["todo_id"], db_path) is True
        assert todo.list_open(db_path) == []

        # Re-running sync on the SAME processed email (as a pipeline re-run
        # would) must not resurrect the completed item.
        todo.sync(persist.all_processed(db_path), db_path)
        assert todo.list_open(db_path) == []

    def test_completing_an_already_done_or_unknown_id_returns_false(self, db_path):
        assert todo.complete("does-not-exist", db_path) is False


class TestNeedsReplyAutoResolves:
    def test_reply_sent_closes_the_open_needs_reply_row(self, db_path):
        persist.upsert([email(reply_outline_status=ReplyOutlineStatus.SUGGESTED)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert any(r["kind"] == "needs_reply" for r in todo.list_open(db_path))

        # The reply gets sent (outside this module's concern how) and the
        # pipeline re-syncs — the row should close itself, not require the
        # user to check it off by hand.
        persist.update_outline("e1", ["bullet"], ReplyOutlineStatus.SENT, db_path)
        todo.sync(persist.all_processed(db_path), db_path)

        assert not any(r["kind"] == "needs_reply" for r in todo.list_open(db_path))


class TestListOpenOrdering:
    def test_most_important_first(self, db_path):
        persist.upsert(
            [
                email("low", action_items=["Low task"], importance_score=10.0,
                      importance_level=ImportanceLevel.LOW),
                email("high", action_items=["High task"], importance_score=90.0,
                      importance_level=ImportanceLevel.URGENT),
            ],
            db_path,
        )
        todo.sync(persist.all_processed(db_path), db_path)

        rows = todo.list_open(db_path)
        assert rows[0]["text"] == "High task"
