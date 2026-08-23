"""SQLite persistence for agent_conversation / agent_message (Checkpoint 0).

Connection/DDL comes from models/db.py, the same shared layer
pipeline/persist.py and ingestion/store.py use. Follows persist.py's
row<->object mapping style: db.connect + a light _prepare at the top of
every public function, db_path=None everywhere for test override.

Round-tripping through the DB (not in-memory state) matters here: the
extension panel lives inside Gmail, a SPA that remounts content scripts
constantly, so anything held only in memory would not survive a user
clicking between messages.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from models import db

UTC = timezone.utc


@dataclass
class Conversation:
    conversation_id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime


def _prepare(conn: sqlite3.Connection) -> None:
    # CONTEXT_SCHEMAS (models/db.py) isn't split into individually-named
    # constants the way RAW_EMAIL_SCHEMA/PROCESSED_EMAIL_SCHEMA are, so this
    # prepares every table rather than just these two -- cheap and
    # idempotent (db.prepare's own contract), not a correctness concern.
    db.prepare(conn)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create(title: Optional[str] = None, db_path: Optional[Path] = None) -> str:
    """Start a new conversation, returning its id."""
    conversation_id = str(uuid.uuid4())
    now = _now_iso()
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.execute(
            "INSERT INTO agent_conversation (conversation_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, title, now, now),
        )
        conn.commit()
    return conversation_id


def get(conversation_id: str, db_path: Optional[Path] = None) -> Optional[Conversation]:
    """One conversation's metadata, or None if it doesn't exist."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        row = conn.execute(
            "SELECT conversation_id, title, created_at, updated_at FROM agent_conversation "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return None
    return Conversation(
        conversation_id=row["conversation_id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def append(
    conversation_id: str,
    role: str,
    content: Any,
    db_path: Optional[Path] = None,
) -> None:
    """Append one message. `content` is whatever shape the caller has —
    a plain string for a simple user turn, or a list of Anthropic content
    blocks (dicts) for an assistant turn or a tool-result turn, matching
    agent.loop.Event.new_messages. Stored as JSON either way; history()
    hands it back in the same shape agent.loop.run's `messages` expects."""
    if role not in ("user", "assistant"):
        raise ValueError("role must be 'user' or 'assistant', got {0!r}".format(role))
    now = _now_iso()
    with db.connect(db_path) as conn:
        _prepare(conn)
        exists = conn.execute(
            "SELECT 1 FROM agent_conversation WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if exists is None:
            raise ValueError("Unknown conversation {0!r}".format(conversation_id))
        conn.execute(
            "INSERT INTO agent_message (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, json.dumps(content), now),
        )
        conn.execute(
            "UPDATE agent_conversation SET updated_at = ? WHERE conversation_id = ?",
            (now, conversation_id),
        )
        conn.commit()


def history(
    conversation_id: str,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Every message in the conversation, oldest first. `limit`, if given,
    keeps the most recent `limit` messages (still returned oldest-first) —
    a long-running conversation shouldn't have to replay its entire history
    into every model call."""
    if limit is None:
        sql = "SELECT role, content FROM agent_message WHERE conversation_id = ? ORDER BY id ASC"
        params: tuple = (conversation_id,)
    else:
        sql = (
            "SELECT role, content FROM ("
            "SELECT id, role, content FROM agent_message WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC"
        )
        params = (conversation_id, limit)
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(sql, params).fetchall()
    return [{"role": row["role"], "content": json.loads(row["content"])} for row in rows]


def recent(limit: int = 20, db_path: Optional[Path] = None) -> List[Conversation]:
    """Most recently updated conversations first."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT conversation_id, title, created_at, updated_at FROM agent_conversation "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        Conversation(
            conversation_id=row["conversation_id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
        for row in rows
    ]
