"""Derives the to-do list from `processed_email` and keeps `todo_item` rows
in sync across pipeline re-runs without resurrecting a completed item.

Two kinds of row:
  - action_item — one per string in ProcessedEmail.action_items
    (summarization/action_items.py).
  - needs_reply — one per email that is not no-reply and hasn't been replied
    to yet (reply_outline_status != SENT).

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
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from models import db
from models.schema import ProcessedEmail, ReplyOutlineStatus, TodoKind, TodoStatus


def _prepare(conn) -> None:
    db.prepare(conn, db.TODO_ITEM_SCHEMA)


def make_todo_id(email_id: str, kind: TodoKind, text: str) -> str:
    digest = hashlib.sha1("|".join([email_id, kind.value, text]).encode("utf-8"))
    return digest.hexdigest()[:24]


def _needs_reply(email: ProcessedEmail) -> bool:
    return email.is_no_reply is False and email.reply_outline_status != ReplyOutlineStatus.SENT


def _needs_reply_text(email: ProcessedEmail) -> str:
    return 'Reply to "{0}"'.format(email.subject or "(no subject)")


def sync(emails: Iterable[ProcessedEmail], db_path: Optional[Path] = None) -> int:
    """Insert any new open todo rows implied by `emails`; never touches an
    existing row's status, except to auto-close a `needs_reply` row whose
    condition no longer holds. Returns the number of rows inserted."""
    emails = list(emails)
    now = datetime.now(timezone.utc).isoformat()

    to_insert: List[tuple] = []
    to_close_email_ids: List[str] = []
    for email in emails:
        for text in email.action_items or []:
            todo_id = make_todo_id(email.email_id, TodoKind.ACTION_ITEM, text)
            to_insert.append((todo_id, email.email_id, TodoKind.ACTION_ITEM.value, text, now))

        if _needs_reply(email):
            text = _needs_reply_text(email)
            todo_id = make_todo_id(email.email_id, TodoKind.NEEDS_REPLY, text)
            to_insert.append((todo_id, email.email_id, TodoKind.NEEDS_REPLY.value, text, now))
        else:
            to_close_email_ids.append(email.email_id)

    with db.connect(db_path) as conn:
        _prepare(conn)
        if to_insert:
            conn.executemany(
                "INSERT INTO todo_item (todo_id, email_id, kind, text, status, created_at) "
                "VALUES (?, ?, ?, ?, 'open', ?) "
                "ON CONFLICT(todo_id) DO NOTHING",
                to_insert,
            )
        if to_close_email_ids:
            conn.executemany(
                "UPDATE todo_item SET status = 'done', completed_at = ? "
                "WHERE email_id = ? AND kind = 'needs_reply' AND status = 'open'",
                [(now, eid) for eid in to_close_email_ids],
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
