"""SQLite persistence for the `processed_email` table (Phase 6, step 2).

Every write is an idempotent upsert keyed on email_id, matching
ingestion/store.py's pattern for raw_email - the pipeline is expected to
be re-run (full or incremental) rather than run exactly once per email.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.db import connect
from models.schema import (
    CalendarContext,
    CalendarSlot,
    ImportanceLevel,
    ProcessedEmail,
    ReadStatus,
    ReplyOutlineStatus,
)

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_email (
    email_id                TEXT PRIMARY KEY,
    thread_id                TEXT NOT NULL,
    sender                    TEXT NOT NULL,
    subject                   TEXT,
    received_at                TEXT NOT NULL,
    read_status                 TEXT NOT NULL,
    is_no_reply                 INTEGER,
    no_reply_reason             TEXT,
    importance_score            REAL,
    importance_level            TEXT,
    importance_justification    TEXT,
    summary                     TEXT,
    is_scheduling_related       INTEGER,
    calendar_context_json       TEXT,
    reply_outline_json          TEXT,
    reply_outline_status        TEXT NOT NULL,
    processed_at                TEXT
);
CREATE INDEX IF NOT EXISTS ix_processed_email_importance
    ON processed_email (importance_score DESC);
"""

_UPSERT = """
INSERT INTO processed_email (
    email_id, thread_id, sender, subject, received_at, read_status,
    is_no_reply, no_reply_reason, importance_score, importance_level,
    importance_justification, summary, is_scheduling_related,
    calendar_context_json, reply_outline_json, reply_outline_status,
    processed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(email_id) DO UPDATE SET
    thread_id                = excluded.thread_id,
    sender                    = excluded.sender,
    subject                   = excluded.subject,
    received_at                = excluded.received_at,
    read_status                 = excluded.read_status,
    is_no_reply                 = excluded.is_no_reply,
    no_reply_reason             = excluded.no_reply_reason,
    importance_score            = excluded.importance_score,
    importance_level            = excluded.importance_level,
    importance_justification    = excluded.importance_justification,
    summary                     = excluded.summary,
    is_scheduling_related       = excluded.is_scheduling_related,
    calendar_context_json       = excluded.calendar_context_json,
    reply_outline_json          = excluded.reply_outline_json,
    reply_outline_status        = excluded.reply_outline_status,
    processed_at                = excluded.processed_at
"""


def _slot_to_dict(slot: CalendarSlot) -> dict:
    return {"start": slot.start.isoformat(), "end": slot.end.isoformat()}


def _slot_from_dict(data: dict) -> CalendarSlot:
    return CalendarSlot(start=datetime.fromisoformat(data["start"]), end=datetime.fromisoformat(data["end"]))


def _calendar_context_to_json(context: Optional[CalendarContext]) -> Optional[str]:
    if context is None:
        return None
    return json.dumps(
        {
            "range_start": context.range_start.isoformat(),
            "range_end": context.range_end.isoformat(),
            "busy_blocks": [_slot_to_dict(s) for s in context.busy_blocks],
            "existing_events": context.existing_events,
            "suggested_slots": [_slot_to_dict(s) for s in context.suggested_slots],
            "generated_at": context.generated_at.isoformat(),
        }
    )


def _calendar_context_from_json(raw: Optional[str]) -> Optional[CalendarContext]:
    if raw is None:
        return None
    data = json.loads(raw)
    return CalendarContext(
        range_start=datetime.fromisoformat(data["range_start"]),
        range_end=datetime.fromisoformat(data["range_end"]),
        busy_blocks=[_slot_from_dict(s) for s in data["busy_blocks"]],
        existing_events=data["existing_events"],
        suggested_slots=[_slot_from_dict(s) for s in data["suggested_slots"]],
        generated_at=datetime.fromisoformat(data["generated_at"]),
    )


def _row_to_processed_email(row) -> ProcessedEmail:
    return ProcessedEmail(
        email_id=row["email_id"],
        thread_id=row["thread_id"],
        sender=row["sender"],
        subject=row["subject"],
        received_at=datetime.fromisoformat(row["received_at"]),
        read_status=ReadStatus(row["read_status"]),
        is_no_reply=bool(row["is_no_reply"]) if row["is_no_reply"] is not None else None,
        no_reply_reason=row["no_reply_reason"],
        importance_score=row["importance_score"],
        importance_level=ImportanceLevel(row["importance_level"]) if row["importance_level"] else None,
        importance_justification=row["importance_justification"],
        summary=row["summary"],
        is_scheduling_related=bool(row["is_scheduling_related"])
        if row["is_scheduling_related"] is not None
        else None,
        calendar_context=_calendar_context_from_json(row["calendar_context_json"]),
        reply_outline=json.loads(row["reply_outline_json"]) if row["reply_outline_json"] else None,
        reply_outline_status=ReplyOutlineStatus(row["reply_outline_status"]),
        processed_at=datetime.fromisoformat(row["processed_at"]) if row["processed_at"] else None,
    )


def upsert_processed_email(processed: ProcessedEmail, db_path: Optional[Path] = None) -> None:
    """Insert or refresh one processed email, keyed on email_id."""
    row = (
        processed.email_id,
        processed.thread_id,
        processed.sender,
        processed.subject,
        processed.received_at.isoformat(),
        processed.read_status.value,
        None if processed.is_no_reply is None else int(processed.is_no_reply),
        processed.no_reply_reason,
        processed.importance_score,
        processed.importance_level.value if processed.importance_level else None,
        processed.importance_justification,
        processed.summary,
        None if processed.is_scheduling_related is None else int(processed.is_scheduling_related),
        _calendar_context_to_json(processed.calendar_context),
        json.dumps(processed.reply_outline) if processed.reply_outline is not None else None,
        processed.reply_outline_status.value,
        processed.processed_at.isoformat() if processed.processed_at else None,
    )
    with connect(db_path or DEFAULT_DB_PATH, schema_ddl=SCHEMA) as conn:
        conn.execute(_UPSERT, row)
        conn.commit()


def get_processed_email(email_id: str, db_path: Optional[Path] = None) -> Optional[ProcessedEmail]:
    """One persisted ProcessedEmail by id, or None."""
    with connect(db_path or DEFAULT_DB_PATH, schema_ddl=SCHEMA) as conn:
        row = conn.execute("SELECT * FROM processed_email WHERE email_id = ?", (email_id,)).fetchone()
    return _row_to_processed_email(row) if row else None


def list_processed_emails(db_path: Optional[Path] = None) -> list[ProcessedEmail]:
    """All persisted emails, most important first."""
    with connect(db_path or DEFAULT_DB_PATH, schema_ddl=SCHEMA) as conn:
        rows = conn.execute(
            "SELECT * FROM processed_email ORDER BY importance_score DESC NULLS LAST"
        ).fetchall()
    return [_row_to_processed_email(r) for r in rows]
