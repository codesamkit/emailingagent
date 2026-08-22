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

from models.schema import ReadStatus

from . import config
from .models import RawEmail

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_email (
    email_id        TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    sender          TEXT NOT NULL,
    recipients      TEXT NOT NULL DEFAULT '[]',
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


# Columns added after the table first shipped, with the DDL to add them.
# Applied on every open so a database written by an earlier version keeps
# working instead of failing on an unknown column — re-ingesting 100+ messages
# just to gain a column is not a reasonable upgrade path.
_MIGRATIONS = (
    ("recipients", "ALTER TABLE raw_email ADD COLUMN recipients TEXT NOT NULL DEFAULT '[]'"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(raw_email)")}
    if not existing:
        return  # fresh database; SCHEMA already created every column
    for column, ddl in _MIGRATIONS:
        if column not in existing:
            conn.execute(ddl)


def _prepare(conn: sqlite3.Connection) -> None:
    """Ensure the table exists and is up to date. Cheap and idempotent."""
    conn.executescript(SCHEMA)
    _migrate(conn)


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
