"""Derives the to-do list from `processed_email` and keeps `todo_item` rows
in sync across pipeline re-runs without resurrecting a completed item.

Two kinds of row:
  - action_item — one per distinct task per THREAD, from
    ProcessedEmail.action_items (summarization/action_items.py). Per thread,
    not per email: a task restated on every "Re:" is one task.
  - needs_reply — one per email that still owes a reply, i.e. passes
    `drafting.outline.is_eligible` (read, not no-reply, classified) and hasn't
    been sent yet. The read gate matters: without it this fires on every
    unread email and the list becomes a copy of the inbox.

`todo_id` is a stable hash of (email_id, kind, text) rather than a random
id, so re-deriving the same item on a later run recognizes the row it
already wrote (INSERT ... ON CONFLICT DO NOTHING) instead of minting a
duplicate — which is what lets a user's completion survive the next
refresh. `needs_reply` rows are the one kind this module also closes on its
own: unlike an action item (only the user can say it's done), whether a
reply is still owed is fully determined by the email's current state, so a
row whose condition no longer holds (the user replied, or a later pass
reclassified the sender as no-reply) is auto-resolved rather than left open
forever.

Action-item rows are reconciled the same way on each run, but only against
the emails in that run: a row the current derivation no longer produces (a
duplicate claimed elsewhere in its thread, or junk predating
`action_items.is_meaningful`) is closed rather than left open forever. Note
what this does NOT do — it never re-keys an existing row, because the todo_id
is the only thing tying a user's completion to an item, and changing the
hash inputs would resurrect everything they had already ticked off.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from models import db
from models.schema import ProcessedEmail, ReplyOutlineStatus, TodoKind, TodoStatus
from summarization.action_items import is_meaningful


def _prepare(conn) -> None:
    db.prepare(conn, db.TODO_ITEM_SCHEMA)


def make_todo_id(email_id: str, kind: TodoKind, text: str) -> str:
    digest = hashlib.sha1("|".join([email_id, kind.value, text]).encode("utf-8"))
    return digest.hexdigest()[:24]


def _needs_reply(email: ProcessedEmail) -> bool:
    """Whether a reply is still owed on this email.

    Reuses `drafting.outline.is_eligible` -- the gate the whole product is
    built on (read AND not no-reply AND classified) -- rather than re-deriving
    a weaker version of it. The hand-rolled check this replaces omitted read
    status, so in a mailbox that is 161-of-163 unread it fired on essentially
    every email and the to-do list became a copy of the inbox.

    `is_eligible` is also what `api/serializers.py` reports as
    `outlineEligible`, so the list and the badge can no longer disagree.
    """
    from drafting.outline import is_eligible

    eligible, _ = is_eligible(email)
    return eligible and email.reply_outline_status != ReplyOutlineStatus.SENT


def _needs_reply_text(email: ProcessedEmail) -> str:
    return 'Reply to "{0}"'.format(email.subject or "(no subject)")


def sync(emails: Iterable[ProcessedEmail], db_path: Optional[Path] = None) -> int:
    """Insert any new open todo rows implied by `emails`; never touches an
    existing row's status, except to auto-close a `needs_reply` row whose
    condition no longer holds. Returns the number of rows inserted."""
    emails = list(emails)
    now = datetime.now(timezone.utc).isoformat()

    to_insert: List[tuple] = []
    # Every todo_id this run believes should be open, across both kinds. The
    # reconcile pass below treats it as authoritative for the batch's emails.
    desired_ids: set = set()
    # (thread_id, lowercased text) -> already claimed. A task restated on every
    # "Re:" in a thread is one task; keying the row on email_id alone counted it
    # once per message. The earliest message in the thread owns it, so the
    # choice is stable across runs rather than depending on batch order.
    claimed: set = set()
    def _order(email: ProcessedEmail):
        return (email.received_at is None, email.received_at, email.email_id)

    for email in sorted(emails, key=_order):
        for raw_text in email.action_items or []:
            text = (raw_text or "").strip()
            # Already rejected at extraction; re-checked because the rows
            # persisted before that guard existed are still in the table.
            if not is_meaningful(text):
                continue
            key = (email.thread_id, text.lower())
            if key in claimed:
                continue
            claimed.add(key)
            todo_id = make_todo_id(email.email_id, TodoKind.ACTION_ITEM, text)
            desired_ids.add(todo_id)
            to_insert.append((todo_id, email.email_id, TodoKind.ACTION_ITEM.value, text, now))

        if _needs_reply(email):
            text = _needs_reply_text(email)
            todo_id = make_todo_id(email.email_id, TodoKind.NEEDS_REPLY, text)
            desired_ids.add(todo_id)
            to_insert.append((todo_id, email.email_id, TodoKind.NEEDS_REPLY.value, text, now))

    with db.connect(db_path) as conn:
        _prepare(conn)
        if to_insert:
            conn.executemany(
                "INSERT INTO todo_item (todo_id, email_id, kind, text, status, created_at) "
                "VALUES (?, ?, ?, ?, 'open', ?) "
                "ON CONFLICT(todo_id) DO NOTHING",
                to_insert,
            )
        # Reconcile: withdraw any open row this run no longer derives. Scoped
        # to the emails in THIS batch, so an incremental run can't touch rows
        # it wasn't asked about, and only open rows, so a user's completion is
        # never disturbed.
        #
        # This covers three things at once: an action item duplicated across a
        # thread, junk predating `is_meaningful`, and -- the subtle one -- a row
        # whose text has since changed. `todo_id` hashes the text, and the
        # needs_reply text embeds the subject, so a change in subject parsing
        # mints a new id and used to leave the old row open forever: every
        # email ended up with two "Reply to ..." items. Deriving the desired
        # set and retiring the rest makes sync idempotent instead.
        #
        # Closed rather than deleted, matching how a lapsed row was always
        # retired, and by todo_id one at a time so a large mailbox can't blow
        # past SQLite's bound-parameter limit.
        batch_ids = {email.email_id for email in emails}
        stale = [
            (now, row["todo_id"])
            for row in conn.execute(
                "SELECT todo_id, email_id FROM todo_item WHERE status = 'open'"
            ).fetchall()
            if row["email_id"] in batch_ids and row["todo_id"] not in desired_ids
        ]
        if stale:
            conn.executemany(
                "UPDATE todo_item SET status = 'done', completed_at = ? WHERE todo_id = ?",
                stale,
            )
        conn.commit()
    return len(to_insert)


def list_open(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Open todo rows, most important email first — unscored last, same
    ordering rule as persist.all_processed."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        db.prepare(conn, db.PROCESSED_EMAIL_SCHEMA)
        rows = conn.execute(
            "SELECT t.todo_id, t.email_id, t.kind, t.text, t.created_at, "
            "       p.thread_id, p.subject, p.sender, p.importance_score, p.importance_level "
            "FROM todo_item t "
            "JOIN processed_email p ON p.email_id = t.email_id "
            "WHERE t.status = 'open' "
            "ORDER BY p.importance_score IS NULL, p.importance_score DESC, t.created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def complete(todo_id: str, db_path: Optional[Path] = None) -> bool:
    """Marks one open todo row done. Returns False if it doesn't exist or is
    already done, so the caller can 404/no-op instead of double-completing."""
    now = datetime.now(timezone.utc).isoformat()
    with db.connect(db_path) as conn:
        _prepare(conn)
        cursor = conn.execute(
            "UPDATE todo_item SET status = 'done', completed_at = ? "
            "WHERE todo_id = ? AND status = 'open'",
            (now, todo_id),
        )
        conn.commit()
        return cursor.rowcount > 0
