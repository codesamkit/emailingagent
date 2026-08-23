"""Persistence for user corrections — the feedback loop's memory.

A correction is one click in the review UI: "this sender's mail is really
<level>" or "this sender is automated / is a real person". Corrections are
stored per event and aggregated into *sender priors* (latest correction per
sender and kind wins), which the pipeline applies deterministically after
every run — see feedback/apply.py.

Deterministic sender priors, not few-shot prompt examples, on purpose: the
measured misclassifications are sender-shaped (a tracker, an order bot, a
newsletter the rules call "personal"), and a stored override fixes every
future email from that sender with certainty, which no prompt example can
promise on a small model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from models import db
from models.schema import ImportanceLevel
from scoring.signals import _addr_only

KIND_LEVEL = "level"
KIND_NO_REPLY = "no_reply"
KINDS = (KIND_LEVEL, KIND_NO_REPLY)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id   TEXT,
    sender     TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('level', 'no_reply')),
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feedback_sender ON feedback (sender, kind, created_at);
"""


def _prepare(conn) -> None:
    conn.executescript(_SCHEMA)


def record(
    sender: str,
    kind: str,
    value: str,
    email_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Store one correction. `sender` may be a full 'Name <addr>' header;
    it is normalized to the bare address."""
    if kind not in KINDS:
        raise ValueError("Unknown feedback kind: {0!r}".format(kind))
    if kind == KIND_LEVEL:
        ImportanceLevel(value)  # raises on garbage before it hits the DB
    elif value not in ("true", "false"):
        raise ValueError("no_reply feedback value must be 'true' or 'false'")

    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.execute(
            "INSERT INTO feedback (email_id, sender, kind, value, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                email_id,
                _addr_only(sender),
                kind,
                value,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def sender_priors(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """{sender_addr: {"level": ImportanceLevel?, "is_no_reply": bool?}} —
    the latest correction per (sender, kind)."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT sender, kind, value FROM feedback ORDER BY created_at, id"
        ).fetchall()

    priors: Dict[str, Dict[str, Any]] = {}
    for sender, kind, value in rows:  # later rows overwrite: latest wins
        entry = priors.setdefault(sender, {})
        if kind == KIND_LEVEL:
            entry["level"] = ImportanceLevel(value)
        else:
            entry["is_no_reply"] = value == "true"
    return priors


def clear(
    sender: str, kind: Optional[str] = None, db_path: Optional[Path] = None
) -> int:
    """Forget corrections for a sender. Returns the number of rows removed.

    Recording is append-only with latest-wins, which makes a correction easy
    to give and — until this existed — impossible to take back. That is not a
    theoretical gap: a five-second pass through the correction buttons left a
    `level` prior on one sender that pinned 161 stored emails to the same
    level, with no way to undo it from the UI and no reason visible in the
    scores. Deleting the rows, rather than appending a "no opinion" marker,
    keeps `sender_priors()` a straight latest-wins read with no third state.

    `kind` limits the clear to 'level' or 'no_reply'; omitted, it forgets
    everything recorded for the sender.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError("Unknown feedback kind: {0!r}".format(kind))

    sql = "DELETE FROM feedback WHERE sender = ?"
    params: list = [_addr_only(sender)]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)

    with db.connect(db_path) as conn:
        _prepare(conn)
        removed = conn.execute(sql, params).rowcount
        conn.commit()
    return int(removed)


def count(db_path: Optional[Path] = None) -> int:
    with db.connect(db_path) as conn:
        _prepare(conn)
        return int(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])
