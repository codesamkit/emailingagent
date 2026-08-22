"""SQLite persistence for the `raw_email` table.

Ingestion is expected to be re-run often (read statuses change, new mail
arrives), so every write is an idempotent upsert keyed on the Gmail message id
rather than a blind insert.

Connection handling, the table DDL, and column migrations live in
`models/db.py` — the shared layer Track A and Track C both build on, so the
pipeline's `processed_email` writes and these `raw_email` writes cannot drift
apart on schema or path resolution. This module owns only the row<->RawEmail
mapping.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from models import db
from models.schema import ReadStatus

from . import config
from .models import RawEmail

# Mutable fields are refreshed on re-ingest; email_id and fetched_at are not.
# read_status especially: an email read in Gmail since the last run must flip,
# because Track C keys reply-outline generation off exactly that field.
_UPSERT = """
INSERT INTO raw_email (
    email_id, thread_id, sender, recipients, subject, body_text, snippet,
    received_at, read_status, label_ids, headers, has_attachments, fetched_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(email_id) DO UPDATE SET
    thread_id       = excluded.thread_id,
    sender          = excluded.sender,
    recipients      = excluded.recipients,
    subject         = excluded.subject,
    body_text       = excluded.body_text,
    snippet         = excluded.snippet,
    received_at     = excluded.received_at,
    read_status     = excluded.read_status,
    label_ids       = excluded.label_ids,
    headers         = excluded.headers,
    has_attachments = excluded.has_attachments,
    fetched_at      = excluded.fetched_at
"""


# Re-exported so existing callers keep working after the move to models/db.py.
connect = db.connect


def _prepare(conn: sqlite3.Connection) -> None:
    """Ensure `raw_email` exists and is up to date. Cheap and idempotent."""
    db.prepare(conn, db.RAW_EMAIL_SCHEMA)


def init_db(db_path: Optional[Path] = None) -> None:
    """Create the `raw_email` table and its indexes if they don't exist."""
    with connect(db_path) as conn:
        _prepare(conn)
        conn.commit()


def upsert_emails(emails: Iterable[RawEmail], db_path: Optional[Path] = None) -> int:
    """Insert or refresh the given emails. Returns the number written."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            e.email_id,
            e.thread_id,
            e.sender,
            json.dumps(e.recipients, ensure_ascii=False),
            e.subject,
            e.body,
            e.snippet,
            # The contract carries a datetime; SQLite gets ISO-8601 text so the
            # received_at index still sorts chronologically as a string.
            e.received_at.isoformat(),
            ReadStatus(e.read_status).value,
            json.dumps(e.label_ids),
            json.dumps(e.headers, ensure_ascii=False),
            1 if e.has_attachments else 0,
            fetched_at,
        )
        for e in emails
    ]
    if not rows:
        return 0
    with connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(_UPSERT, rows)
        conn.commit()
    return len(rows)


def _column(row: sqlite3.Row, name: str, default=None):
    """Read a column that may be absent in a database written pre-migration."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _row_to_email(row: sqlite3.Row) -> RawEmail:
    return RawEmail(
        email_id=row["email_id"],
        thread_id=row["thread_id"],
        sender=row["sender"],
        recipients=json.loads(_column(row, "recipients", "[]")),
        subject=row["subject"] or "",
        body=row["body_text"] or "",
        snippet=row["snippet"] or "",
        received_at=datetime.fromisoformat(row["received_at"]),
        read_status=ReadStatus(row["read_status"]),
        label_ids=json.loads(row["label_ids"]),
        headers=json.loads(row["headers"]),
        has_attachments=bool(row["has_attachments"]),
    )


def count(db_path: Optional[Path] = None) -> int:
    """Total stored emails."""
    with connect(db_path) as conn:
        _prepare(conn)
        return int(conn.execute("SELECT COUNT(*) FROM raw_email").fetchone()[0])


def recent(limit: int = 5, db_path: Optional[Path] = None) -> List[RawEmail]:
    """The most recently received stored emails, newest first."""
    with connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT * FROM raw_email ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_email(r) for r in rows]


def get(email_id: str, db_path: Optional[Path] = None) -> Optional[RawEmail]:
    """One stored email by Gmail message id, or None."""
    with connect(db_path) as conn:
        _prepare(conn)
        row = conn.execute(
            "SELECT * FROM raw_email WHERE email_id = ?", (email_id,)
        ).fetchone()
    return _row_to_email(row) if row else None
