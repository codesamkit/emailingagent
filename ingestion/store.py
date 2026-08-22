"""SQLite persistence for the `raw_email` table.

Ingestion is expected to be re-run often (read statuses change, new mail
arrives), so every write is an idempotent upsert keyed on the Gmail message id
rather than a blind insert.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from . import config
from .models import RawEmail

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_email (
    email_id        TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    sender          TEXT NOT NULL,
    subject         TEXT,
    body_text       TEXT,
    snippet         TEXT,
    received_at     TEXT NOT NULL,
    read_status     TEXT NOT NULL CHECK (read_status IN ('read', 'unread')),
    label_ids       TEXT NOT NULL,
    headers         TEXT NOT NULL,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_raw_email_received_at ON raw_email (received_at DESC);
CREATE INDEX IF NOT EXISTS ix_raw_email_thread ON raw_email (thread_id);
"""

# Mutable fields are refreshed on re-ingest; email_id and fetched_at are not.
# read_status especially: an email read in Gmail since the last run must flip,
# because Track C keys reply-outline generation off exactly that field.
_UPSERT = """
INSERT INTO raw_email (
    email_id, thread_id, sender, subject, body_text, snippet,
    received_at, read_status, label_ids, headers, has_attachments, fetched_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(email_id) DO UPDATE SET
    thread_id       = excluded.thread_id,
    sender          = excluded.sender,
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


def _resolve(db_path: Optional[Path]) -> Path:
    return Path(db_path) if db_path is not None else config.DB_PATH


@contextmanager
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Open the ingestion database, creating its parent directory if needed."""
    path = _resolve(db_path)
    if path.parent and str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Create the `raw_email` table and its indexes if they don't exist."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def upsert_emails(emails: Iterable[RawEmail], db_path: Optional[Path] = None) -> int:
    """Insert or refresh the given emails. Returns the number written."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            e.email_id,
            e.thread_id,
            e.sender,
            e.subject,
            e.body_text,
            e.snippet,
            e.received_at,
            e.read_status,
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
        conn.executescript(SCHEMA)
        conn.executemany(_UPSERT, rows)
        conn.commit()
    return len(rows)


def _row_to_email(row: sqlite3.Row) -> RawEmail:
    return RawEmail(
        email_id=row["email_id"],
        thread_id=row["thread_id"],
        sender=row["sender"],
        subject=row["subject"] or "",
        body_text=row["body_text"] or "",
        snippet=row["snippet"] or "",
        received_at=row["received_at"],
        read_status=row["read_status"],
        label_ids=json.loads(row["label_ids"]),
        headers=json.loads(row["headers"]),
        has_attachments=bool(row["has_attachments"]),
    )


def count(db_path: Optional[Path] = None) -> int:
    """Total stored emails."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        return int(conn.execute("SELECT COUNT(*) FROM raw_email").fetchone()[0])


def recent(limit: int = 5, db_path: Optional[Path] = None) -> List[RawEmail]:
    """The most recently received stored emails, newest first."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        rows = conn.execute(
            "SELECT * FROM raw_email ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_email(r) for r in rows]


def get(email_id: str, db_path: Optional[Path] = None) -> Optional[RawEmail]:
    """One stored email by Gmail message id, or None."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT * FROM raw_email WHERE email_id = ?", (email_id,)
        ).fetchone()
    return _row_to_email(row) if row else None
