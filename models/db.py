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

    processed_at             TEXT,
    context_processed_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_processed_importance ON processed_email (importance_score DESC);
CREATE INDEX IF NOT EXISTS ix_processed_received_at ON processed_email (received_at DESC);
CREATE INDEX IF NOT EXISTS ix_processed_read_status ON processed_email (read_status);
"""

# --- Context graph ---------------------------------------------------------
# Added post-Phase 8 (PHASES-COMPLEX.md Checkpoint 0). All new tables, so
# nothing goes in MIGRATIONS: that dict exists only for columns added to a
# table that already shipped, and CREATE TABLE IF NOT EXISTS handles the rest.
#
# Vectors are float32 little-endian BLOBs read with numpy rather than a vector
# extension: this interpreter's sqlite3 is built without
# enable_load_extension, so sqlite-vec / sqlite-vss cannot be loaded at all.
# At corpus scale (~1,500 chunks x 768 dims = ~4.6 MB) a brute-force dot
# product over one contiguous matrix is ~5 ms, so an ANN index would be
# complexity with no payoff. FTS5 *is* available and is used for real.

CHUNK_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk (
    chunk_id   TEXT PRIMARY KEY,
    email_id   TEXT NOT NULL,
    ord        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('body', 'quoted', 'signature'))
);
CREATE INDEX IF NOT EXISTS ix_chunk_email ON chunk (email_id);
"""

# External-content FTS5: the index stores only the inverted terms and reads
# the column values back out of `chunk` by rowid, so body text is not stored
# twice. That makes the triggers below mandatory rather than a convenience —
# without them the index and the table drift apart silently and MATCH returns
# rows whose text no longer exists.
CHUNK_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    content='chunk',
    content_rowid='rowid',
    tokenize='unicode61'
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
"""

CHUNK_VEC_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_vec (
    chunk_id   TEXT PRIMARY KEY,
    dim        INTEGER NOT NULL,
    vec        BLOB NOT NULL
);
"""

# The UNIQUE index is the deterministic half of entity resolution: an exact
# normalized_key match within a kind must be an upsert, not a second node.
# Scoped by kind on purpose — a PERSON called "Atlas" and a PROJECT called
# "Atlas" are two different things and must never collapse into one.
ENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
    entity_id       TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,
    normalized_key  TEXT NOT NULL,
    first_seen      TEXT,
    last_seen       TEXT,
    mention_count   INTEGER NOT NULL DEFAULT 0,
    salience        REAL NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_kind_key ON entity (kind, normalized_key);
CREATE INDEX IF NOT EXISTS ix_entity_kind ON entity (kind);
"""

ENTITY_ALIAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_alias (
    entity_id        TEXT NOT NULL,
    alias            TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY (entity_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS ix_entity_alias_norm ON entity_alias (normalized_alias);
"""

ENTITY_VEC_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_vec (
    entity_id  TEXT PRIMARY KEY,
    dim        INTEGER NOT NULL,
    vec        BLOB NOT NULL
);
"""

MENTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS mention (
    mention_id  TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL,
    email_id    TEXT NOT NULL,
    chunk_id    TEXT,
    span_text   TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    source      TEXT NOT NULL CHECK (source IN ('header', 'regex', 'llm'))
);
CREATE INDEX IF NOT EXISTS ix_mention_email ON mention (email_id);
CREATE INDEX IF NOT EXISTS ix_mention_entity ON mention (entity_id);
"""

RELATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS relation (
    src_entity_id      TEXT NOT NULL,
    dst_entity_id      TEXT NOT NULL,
    rel                TEXT NOT NULL,
    weight             REAL NOT NULL DEFAULT 1.0,
    evidence_email_ids TEXT NOT NULL DEFAULT '[]',   -- JSON array
    PRIMARY KEY (src_entity_id, dst_entity_id, rel)
);
CREATE INDEX IF NOT EXISTS ix_relation_src ON relation (src_entity_id);
CREATE INDEX IF NOT EXISTS ix_relation_dst ON relation (dst_entity_id);
"""

# Keyed on (node_type, node_id), not node_id alone: node_id is a Gmail
# thread_id for THREAD briefs and an entity_id for the other three, and the
# read path is get_brief(node_type, node_id), so the composite key is the
# real identity. evidence_hash is the cache key that keeps a brief from being
# regenerated — an unchanged hash means an unchanged answer.
NODE_BRIEF_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_brief (
    node_type          TEXT NOT NULL,
    node_id            TEXT NOT NULL,
    headline           TEXT,
    body_md            TEXT,
    open_items         TEXT NOT NULL DEFAULT '[]',   -- JSON array
    evidence_email_ids TEXT NOT NULL DEFAULT '[]',   -- JSON array
    evidence_hash      TEXT,
    generated_at       TEXT,
    PRIMARY KEY (node_type, node_id)
);
CREATE INDEX IF NOT EXISTS ix_node_brief_type ON node_brief (node_type);
"""

# The in-app agent's chat log. It lives in the database rather than in the
# extension because the panel is injected into Gmail, a SPA that remounts
# content scripts constantly — in-memory chat state does not survive the user
# clicking between messages.
AGENT_CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_conversation (
    conversation_id TEXT PRIMARY KEY,
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_conversation_updated
    ON agent_conversation (updated_at DESC);
"""

AGENT_MESSAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_message (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,                  -- JSON content blocks
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_message_conversation
    ON agent_message (conversation_id, id);
"""

CONTEXT_SCHEMAS: Tuple[str, ...] = (
    CHUNK_SCHEMA,
    CHUNK_FTS_SCHEMA,
    CHUNK_VEC_SCHEMA,
    ENTITY_SCHEMA,
    ENTITY_ALIAS_SCHEMA,
    ENTITY_VEC_SCHEMA,
    MENTION_SCHEMA,
    RELATION_SCHEMA,
    NODE_BRIEF_SCHEMA,
    AGENT_CONVERSATION_SCHEMA,
    AGENT_MESSAGE_SCHEMA,
)

ALL_SCHEMAS: Tuple[str, ...] = (
    RAW_EMAIL_SCHEMA,
    PROCESSED_EMAIL_SCHEMA,
) + CONTEXT_SCHEMAS

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
        (
            "mentioned_dates",
            "ALTER TABLE processed_email ADD COLUMN mentioned_dates TEXT",
        ),
        (
            "context_processed_at",
            "ALTER TABLE processed_email ADD COLUMN context_processed_at TEXT",
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
