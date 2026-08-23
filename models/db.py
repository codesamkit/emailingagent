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
    mentioned_dates          TEXT,          -- JSON array of date strings, verbatim

    category                 TEXT,

    is_scheduling_related    INTEGER,
    calendar_context         TEXT,          -- JSON blob of CalendarContext

    proposed_event           TEXT,          -- JSON blob of ProposedEvent
    proposed_event_status    TEXT NOT NULL DEFAULT 'none',

    reply_outline            TEXT,          -- JSON array of bullet strings
    reply_outline_status     TEXT NOT NULL DEFAULT 'none',
    reply_draft              TEXT,          -- full prose, auto-expanded from reply_outline

    processed_at             TEXT,
    context_processed_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_processed_importance ON processed_email (importance_score DESC);
CREATE INDEX IF NOT EXISTS ix_processed_received_at ON processed_email (received_at DESC);
CREATE INDEX IF NOT EXISTS ix_processed_read_status ON processed_email (read_status);
"""

# --- Context graph (Checkpoint 0, PHASES-COMPLEX.md) -----------------------
#
# Eleven new tables, none of which existed before this checkpoint, so none
# get a MIGRATIONS entry below — that dict is only for columns added to a
# table that already shipped; a brand-new table's CREATE already has every
# column. Mirrors models/schema.py's Chunk/Entity/Mention/Relation/Brief/
# agent_conversation/agent_message additions 1:1.

CONTEXT_SCHEMAS: Tuple[str, ...] = (
    # chunk_fts is this repo's first FTS5 table: an external-content virtual
    # table over chunk.text, kept in sync by the three triggers below rather
    # than duplicating the text into the index.
    """
CREATE TABLE IF NOT EXISTS chunk (
    chunk_id    TEXT PRIMARY KEY,
    email_id    TEXT NOT NULL,
    ord         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('body', 'quoted', 'signature'))
);
CREATE INDEX IF NOT EXISTS ix_chunk_email ON chunk (email_id);
""",
    """
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    content='chunk',
    content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS chunk_fts_ai AFTER INSERT ON chunk BEGIN
    INSERT INTO chunk_fts (rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunk_fts_ad AFTER DELETE ON chunk BEGIN
    INSERT INTO chunk_fts (chunk_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunk_fts_au AFTER UPDATE ON chunk BEGIN
    INSERT INTO chunk_fts (chunk_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO chunk_fts (rowid, text) VALUES (new.rowid, new.text);
END;
""",
    """
CREATE TABLE IF NOT EXISTS chunk_vec (
    chunk_id    TEXT PRIMARY KEY,
    dim         INTEGER NOT NULL,
    vec         BLOB NOT NULL           -- float32 little-endian
);
""",
    """
CREATE TABLE IF NOT EXISTS entity (
    entity_id       TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (
        kind IN ('person', 'org', 'case', 'project', 'deliverable', 'document', 'topic')
    ),
    canonical_name  TEXT NOT NULL,
    normalized_key  TEXT NOT NULL,
    first_seen      TEXT,
    last_seen       TEXT,
    mention_count   INTEGER NOT NULL DEFAULT 0,
    salience        REAL NOT NULL DEFAULT 0.0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_kind_key ON entity (kind, normalized_key);
""",
    """
CREATE TABLE IF NOT EXISTS entity_alias (
    entity_id          TEXT NOT NULL,
    alias              TEXT NOT NULL,
    normalized_alias   TEXT NOT NULL,
    PRIMARY KEY (entity_id, alias)
);
CREATE INDEX IF NOT EXISTS ix_entity_alias_normalized ON entity_alias (normalized_alias);
""",
    """
CREATE TABLE IF NOT EXISTS entity_vec (
    entity_id   TEXT PRIMARY KEY,
    vec         BLOB NOT NULL           -- float32 little-endian
);
""",
    """
CREATE TABLE IF NOT EXISTS mention (
    mention_id  TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL,
    email_id    TEXT NOT NULL,
    chunk_id    TEXT,
    span_text   TEXT NOT NULL,
    confidence  REAL NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('header', 'regex', 'llm'))
);
CREATE INDEX IF NOT EXISTS ix_mention_email ON mention (email_id);
CREATE INDEX IF NOT EXISTS ix_mention_entity ON mention (entity_id);
""",
    """
CREATE TABLE IF NOT EXISTS relation (
    src_entity_id        TEXT NOT NULL,
    dst_entity_id        TEXT NOT NULL,
    rel                  TEXT NOT NULL CHECK (
        rel IN ('belongs_to', 'participant_in', 'mentions', 'owner_of')
    ),
    weight               REAL NOT NULL DEFAULT 0.0,
    evidence_email_ids   TEXT NOT NULL DEFAULT '[]',   -- JSON array of email ids
    PRIMARY KEY (src_entity_id, dst_entity_id, rel)
);
""",
    """
CREATE TABLE IF NOT EXISTS node_brief (
    node_type            TEXT NOT NULL CHECK (
        node_type IN ('thread', 'case', 'project', 'person')
    ),
    node_id              TEXT NOT NULL,
    headline             TEXT,
    body_md              TEXT,
    open_items           TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    evidence_email_ids   TEXT NOT NULL DEFAULT '[]',   -- JSON array of email ids
    evidence_hash        TEXT,
    generated_at         TEXT,
    PRIMARY KEY (node_type, node_id)
);
""",
    """
CREATE TABLE IF NOT EXISTS agent_conversation (
    conversation_id   TEXT PRIMARY KEY,
    title             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
""",
    """
CREATE TABLE IF NOT EXISTS agent_message (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id    TEXT NOT NULL,
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content            TEXT NOT NULL,      -- JSON: list of Anthropic content blocks
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_message_conversation ON agent_message (conversation_id);
""",
)

ALL_SCHEMAS: Tuple[str, ...] = (RAW_EMAIL_SCHEMA, PROCESSED_EMAIL_SCHEMA) + CONTEXT_SCHEMAS

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
        (
            "mentioned_dates",
            "ALTER TABLE processed_email ADD COLUMN mentioned_dates TEXT",
        ),
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
        (
            "context_processed_at",
            "ALTER TABLE processed_email ADD COLUMN context_processed_at TEXT",
        ),
        (
            "reply_draft",
            "ALTER TABLE processed_email ADD COLUMN reply_draft TEXT",
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
