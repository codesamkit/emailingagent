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


class TestNeedsReplyGate:
    """The gate is `drafting.outline.is_eligible`, not a local re-derivation.
    Without the read half, an unread-heavy mailbox turns the list into a copy
    of the inbox -- 161 of 163 emails in the demo data."""

    def _kinds(self, db_path):
        return [r["kind"] for r in todo.list_open(db_path)]

    def test_unread_email_owes_no_reply_yet(self, db_path):
        persist.upsert([email(read_status=ReadStatus.UNREAD)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert "needs_reply" not in self._kinds(db_path)

    def test_read_email_owes_a_reply(self, db_path):
        persist.upsert([email(read_status=ReadStatus.READ)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert "needs_reply" in self._kinds(db_path)

    def test_no_reply_sender_owes_nothing(self, db_path):
        persist.upsert([email(is_no_reply=True)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert "needs_reply" not in self._kinds(db_path)

    def test_unclassified_email_owes_nothing(self, db_path):
        """is_no_reply is None: classification hasn't run, so a no-reply
        sender could still be hiding in there."""
        persist.upsert([email(is_no_reply=None)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert "needs_reply" not in self._kinds(db_path)

    def test_an_already_open_row_is_withdrawn_once_it_stops_qualifying(self, db_path):
        """The 161 rows already in a real database have to go away on the next
        run, not linger because they were written under the old rule."""
        persist.upsert([email(read_status=ReadStatus.READ)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert "needs_reply" in self._kinds(db_path)

        persist.upsert([email(read_status=ReadStatus.UNREAD)], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert "needs_reply" not in self._kinds(db_path)


class TestActionItemHygiene:
    def _texts(self, db_path):
        return [r["text"] for r in todo.list_open(db_path) if r["kind"] == "action_item"]

    def test_junk_never_becomes_a_todo(self, db_path):
        persist.upsert([email(action_items=[",", "']}  deficiency:'", "Send the contract"])], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        assert self._texts(db_path) == ["Send the contract"]

    def test_a_task_restated_across_a_thread_counts_once(self, db_path):
        task = "Send the signed contract by Friday."
        persist.upsert(
            [
                email("a", thread_id="t1", action_items=[task]),
                email("b", thread_id="t1", action_items=[task]),
                email("c", thread_id="t1", action_items=[task, "Book the room"]),
            ],
            db_path,
        )
        todo.sync(persist.all_processed(db_path), db_path)
        assert sorted(self._texts(db_path)) == ["Book the room", task]

    def test_the_same_task_in_a_different_thread_is_its_own_item(self, db_path):
        task = "Send the signed contract by Friday."
        persist.upsert(
            [email("a", thread_id="t1", action_items=[task]),
             email("b", thread_id="t2", action_items=[task])],
            db_path,
        )
        todo.sync(persist.all_processed(db_path), db_path)
        assert self._texts(db_path) == [task, task]

    def test_duplicates_already_persisted_are_withdrawn_on_the_next_run(self, db_path):
        """The 37 duplicate rows in a real database predate the dedup, so the
        reconcile pass has to retire them rather than only preventing new ones."""
        task = "Send the signed contract by Friday."
        rows = [email("a", thread_id="t1", action_items=[task]),
                email("b", thread_id="t1", action_items=[task])]
        persist.upsert(rows, db_path)
        # Write both rows the way the old, per-email keying did.
        from models import db as models_db
        from models.schema import TodoKind
        with models_db.connect(db_path) as conn:
            models_db.prepare(conn, models_db.TODO_ITEM_SCHEMA)
            for eid in ("a", "b"):
                conn.execute(
                    "INSERT INTO todo_item (todo_id, email_id, kind, text, status, created_at) "
                    "VALUES (?, ?, 'action_item', ?, 'open', ?)",
                    (todo.make_todo_id(eid, TodoKind.ACTION_ITEM, task), eid, task, NOW.isoformat()),
                )
            conn.commit()
        assert len(self._texts(db_path)) == 2

        todo.sync(persist.all_processed(db_path), db_path)
        assert self._texts(db_path) == [task]

    def test_a_completed_item_is_not_resurrected(self, db_path):
        """The whole reason the id scheme was left alone: re-keying rows would
        un-tick everything the user had already completed."""
        persist.upsert([email(action_items=["Send the contract"])], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        todo_id = todo.list_open(db_path)[0]["todo_id"]
        assert todo.complete(todo_id, db_path)

        todo.sync(persist.all_processed(db_path), db_path)
        assert todo_id not in {r["todo_id"] for r in todo.list_open(db_path)}

    def test_reconcile_leaves_other_emails_alone(self, db_path):
        """An incremental run syncs a subset; it must not retire rows belonging
        to emails it wasn't asked about."""
        persist.upsert(
            [email("a", thread_id="t1", action_items=["Task A"]),
             email("b", thread_id="t2", action_items=["Task B"])],
            db_path,
        )
        todo.sync(persist.all_processed(db_path), db_path)
        assert sorted(self._texts(db_path)) == ["Task A", "Task B"]

        only_a = [e for e in persist.all_processed(db_path) if e.email_id == "a"]
        todo.sync(only_a, db_path)
        assert sorted(self._texts(db_path)) == ["Task A", "Task B"]

    def test_a_changed_subject_does_not_leave_a_second_reply_row(self, db_path):
        """todo_id hashes the text and the needs_reply text embeds the subject,
        so a change in subject parsing used to mint a new row and leave the old
        one open -- every email ended up with two "Reply to ..." items."""
        persist.upsert([email(subject="[sender@x.com] Re: Alpha")], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        first = [r for r in todo.list_open(db_path) if r["kind"] == "needs_reply"]
        assert len(first) == 1

        persist.upsert([email(subject="Re: Alpha")], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        second = [r for r in todo.list_open(db_path) if r["kind"] == "needs_reply"]
        assert len(second) == 1
        assert second[0]["text"] == 'Reply to "Re: Alpha"'

    def test_sync_is_idempotent(self, db_path):
        persist.upsert([email(action_items=["Send the contract"])], db_path)
        todo.sync(persist.all_processed(db_path), db_path)
        before = {r["todo_id"] for r in todo.list_open(db_path)}
        for _ in range(3):
            todo.sync(persist.all_processed(db_path), db_path)
        assert {r["todo_id"] for r in todo.list_open(db_path)} == before

