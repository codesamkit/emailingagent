"""Shared SQLite layer — connection, schema, and migrations.

Part of the frozen shared contract (see FILE-TREE.md). It exists so that
`ingestion/store.py` (Track A) and `pipeline/persist.py` (Track C) do not each
hand-roll their own connection handling, path resolution, and DDL — the DRY
rule in CLAUDE.md. Each track still owns its own row<->object mapping; only
the plumbing is shared.

SQLite is the local-dev store. Everything here goes through `connect()` and
the DDL registry below, so swapping in Postgres later means changing this
module rather than hunting for `sqlite3.connect` across the repo.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# One database for the whole app. `ingestion/config.py` reads the same env var,
# so ingestion and the pipeline land in the same file by default.
DEFAULT_DB_PATH = Path(
    os.environ.get("EMAIL_AGENT_DB") or REPO_ROOT / "ingestion" / "data" / "emails.db"
).expanduser()


# --- Schema ----------------------------------------------------------------

RAW_EMAIL_SCHEMA = """
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

# Mirrors models/schema.py's ProcessedEmail. Nullable columns stay NULL until
# the stage that owns them has run, which is what makes incremental re-runs
# (Phase 6) able to tell "not processed yet" from "processed, result was none".
PROCESSED_EMAIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_email (
    email_id                 TEXT PRIMARY KEY,
    thread_id                TEXT NOT NULL,
    sender                   TEXT NOT NULL,
    subject                  TEXT,
    received_at              TEXT NOT NULL,
    read_status              TEXT NOT NULL CHECK (read_status IN ('read', 'unread')),

    is_no_reply              INTEGER,
    no_reply_reason          TEXT,

    importance_score         REAL,
    importance_level         TEXT,
    importance_justification TEXT,

    summary                  TEXT,

    category                 TEXT,

    is_scheduling_related    INTEGER,
    calendar_context         TEXT,          -- JSON blob of CalendarContext

    proposed_event           TEXT,          -- JSON blob of ProposedEvent
    proposed_event_status    TEXT NOT NULL DEFAULT 'none',

    reply_outline            TEXT,          -- JSON array of bullet strings
    reply_outline_status     TEXT NOT NULL DEFAULT 'none',

    processed_at             TEXT
);
CREATE INDEX IF NOT EXISTS ix_processed_importance ON processed_email (importance_score DESC);
CREATE INDEX IF NOT EXISTS ix_processed_received_at ON processed_email (received_at DESC);
CREATE INDEX IF NOT EXISTS ix_processed_read_status ON processed_email (read_status);
"""

ALL_SCHEMAS: Tuple[str, ...] = (RAW_EMAIL_SCHEMA, PROCESSED_EMAIL_SCHEMA)

# Columns added after a table first shipped: {table: ((column, DDL), ...)}.
# Applied on every open so a database written by an earlier version keeps
# working instead of failing on an unknown column.
MIGRATIONS: Dict[str, Sequence[Tuple[str, str]]] = {
    "raw_email": (
        (
            "recipients",
            "ALTER TABLE raw_email ADD COLUMN recipients TEXT NOT NULL DEFAULT '[]'",
        ),
    ),
    "processed_email": (
        ("category", "ALTER TABLE processed_email ADD COLUMN category TEXT"),
        (
            "proposed_event",
            "ALTER TABLE processed_email ADD COLUMN proposed_event TEXT",
        ),
        (
            "proposed_event_status",
            "ALTER TABLE processed_email ADD COLUMN proposed_event_status "
            "TEXT NOT NULL DEFAULT 'none'",
        ),
    ),
}


# --- Connection ------------------------------------------------------------

def resolve_path(db_path: Optional[Path] = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


@contextmanager
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Open the app database, creating its parent directory if needed."""
    path = resolve_path(db_path)
    if str(path) != ":memory:" and path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Without this, a foreign-key-free schema still benefits: WAL lets the API
    # read while a pipeline run writes, instead of blocking on a locked file.
    if str(path) != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:  # pragma: no cover - unusual filesystems
            pass
    try:
        yield conn
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection, table: str) -> None:
    """Add any columns missing from an older copy of `table`."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info({0})".format(table))}
    if not existing:
        return  # table doesn't exist yet; the CREATE will make every column
    for column, ddl in MIGRATIONS.get(table, ()):
        if column not in existing:
            conn.execute(ddl)


def prepare(conn: sqlite3.Connection, *schemas: str) -> None:
    """Create the given tables if absent and bring them up to date.

    Cheap and idempotent, so call sites can just call it rather than tracking
    whether initialization has already happened.
    """
    for schema in schemas or ALL_SCHEMAS:
        conn.executescript(schema)
    for table in MIGRATIONS:
        migrate(conn, table)


def init_db(db_path: Optional[Path] = None) -> None:
    """Create every table the app uses."""
    with connect(db_path) as conn:
        prepare(conn)
        conn.commit()
