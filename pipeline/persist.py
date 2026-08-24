"""SQLite persistence for the `processed_email` table.

Connection handling, DDL, and migrations come from `models/db.py` — the same
shared layer `ingestion/store.py` uses, so the two tables cannot drift apart
on path resolution or schema management. This module owns only the
row<->ProcessedEmail mapping.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from models import db
from models.schema import (
    CalendarContext,
    CalendarSlot,
    ImportanceLevel,
    ProcessedEmail,
    ProposedEvent,
    ProposedEventStatus,
    ReadStatus,
    ReplyOutlineStatus,
)

_UPSERT = """
INSERT INTO processed_email (
    email_id, thread_id, sender, subject, received_at, read_status,
    is_no_reply, no_reply_reason,
    importance_score, importance_level, importance_justification,
    summary, mentioned_dates, action_items, category, is_scheduling_related, calendar_context,
    proposed_event, proposed_event_status,
    reply_outline, reply_outline_status, reply_draft, processed_at, context_processed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(email_id) DO UPDATE SET
    thread_id                = excluded.thread_id,
    sender                   = excluded.sender,
    subject                  = excluded.subject,
    received_at              = excluded.received_at,
    read_status              = excluded.read_status,
    is_no_reply              = excluded.is_no_reply,
    no_reply_reason          = excluded.no_reply_reason,
    importance_score         = excluded.importance_score,
    importance_level         = excluded.importance_level,
    importance_justification = excluded.importance_justification,
    summary                  = excluded.summary,
    mentioned_dates          = excluded.mentioned_dates,
    action_items             = excluded.action_items,
    category                 = excluded.category,
    is_scheduling_related    = excluded.is_scheduling_related,
    calendar_context         = excluded.calendar_context,
    proposed_event           = excluded.proposed_event,
    proposed_event_status    = excluded.proposed_event_status,
    reply_outline            = excluded.reply_outline,
    reply_outline_status     = excluded.reply_outline_status,
    reply_draft              = excluded.reply_draft,
    processed_at             = excluded.processed_at,
    context_processed_at     = excluded.context_processed_at
"""


def _prepare(conn: sqlite3.Connection) -> None:
    db.prepare(conn, db.PROCESSED_EMAIL_SCHEMA)


def init_db(db_path: Optional[Path] = None) -> None:
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.commit()


# --- serialization ---------------------------------------------------------

def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _slot_to_dict(slot: CalendarSlot) -> Dict[str, str]:
    return {"start": slot.start.isoformat(), "end": slot.end.isoformat()}


def _slot_from_dict(raw: Dict[str, str]) -> CalendarSlot:
    return CalendarSlot(
        start=datetime.fromisoformat(raw["start"]),
        end=datetime.fromisoformat(raw["end"]),
    )


def _encode_calendar_context(context: Optional[CalendarContext]) -> Optional[str]:
    """CalendarContext -> JSON.

    Hand-rolled rather than `asdict` + a datetime encoder because
    `existing_events` holds free-form dicts whose datetime values also need
    converting; round-tripping is easier to reason about written out.
    """
    if context is None:
        return None
    return json.dumps(
        {
            "range_start": context.range_start.isoformat(),
            "range_end": context.range_end.isoformat(),
            "busy_blocks": [_slot_to_dict(s) for s in context.busy_blocks],
            "suggested_slots": [_slot_to_dict(s) for s in context.suggested_slots],
            "generated_at": context.generated_at.isoformat(),
            "existing_events": [
                {
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in event.items()
                }
                for event in context.existing_events
            ],
        }
    )


def _decode_calendar_context(blob: Optional[str]) -> Optional[CalendarContext]:
    if not blob:
        return None
    data = json.loads(blob)

    def _event(raw: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(raw)
        for key in ("start", "end"):
            if isinstance(out.get(key), str):
                try:
                    out[key] = datetime.fromisoformat(out[key])
                except ValueError:
                    pass
        return out

    return CalendarContext(
        range_start=datetime.fromisoformat(data["range_start"]),
        range_end=datetime.fromisoformat(data["range_end"]),
        busy_blocks=[_slot_from_dict(s) for s in data.get("busy_blocks", [])],
        suggested_slots=[_slot_from_dict(s) for s in data.get("suggested_slots", [])],
        existing_events=[_event(e) for e in data.get("existing_events", [])],
        generated_at=datetime.fromisoformat(data["generated_at"]),
    )


def _encode_proposed_event(event: Optional[ProposedEvent]) -> Optional[str]:
    if event is None:
        return None
    return json.dumps(
        {
            "title": event.title,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "attendees": event.attendees,
            "location": event.location,
            "description": event.description,
            "google_event_id": event.google_event_id,
            "error": event.error,
        }
    )


def _decode_proposed_event(blob: Optional[str]) -> Optional[ProposedEvent]:
    if not blob:
        return None
    data = json.loads(blob)
    return ProposedEvent(
        title=data["title"],
        start=datetime.fromisoformat(data["start"]),
        end=datetime.fromisoformat(data["end"]),
        attendees=data.get("attendees") or [],
        location=data.get("location"),
        description=data.get("description"),
        google_event_id=data.get("google_event_id"),
        error=data.get("error"),
    )


def _to_row(email: ProcessedEmail, processed_at: Optional[datetime]) -> tuple:
    return (
        email.email_id,
        email.thread_id,
        email.sender,
        email.subject,
        email.received_at.isoformat(),
        ReadStatus(email.read_status).value,
        None if email.is_no_reply is None else int(email.is_no_reply),
        email.no_reply_reason,
        email.importance_score,
        email.importance_level.value if email.importance_level else None,
        email.importance_justification,
        email.summary,
        json.dumps(email.mentioned_dates) if email.mentioned_dates is not None else None,
        json.dumps(email.action_items) if email.action_items is not None else None,
        email.category,
        None if email.is_scheduling_related is None else int(email.is_scheduling_related),
        _encode_calendar_context(email.calendar_context),
        _encode_proposed_event(email.proposed_event),
        ProposedEventStatus(email.proposed_event_status).value,
        json.dumps(email.reply_outline) if email.reply_outline is not None else None,
        ReplyOutlineStatus(email.reply_outline_status).value,
        email.reply_draft,
        _dt(processed_at if processed_at is not None else email.processed_at),
        _dt(email.context_processed_at),
    )


def _row_to_email(row: sqlite3.Row) -> ProcessedEmail:
    def _flag(name: str) -> Optional[bool]:
        value = row[name]
        return None if value is None else bool(value)

    return ProcessedEmail(
        email_id=row["email_id"],
        thread_id=row["thread_id"],
        sender=row["sender"],
        subject=row["subject"] or "",
        received_at=datetime.fromisoformat(row["received_at"]),
        read_status=ReadStatus(row["read_status"]),
        is_no_reply=_flag("is_no_reply"),
        no_reply_reason=row["no_reply_reason"],
        importance_score=row["importance_score"],
        importance_level=(
            ImportanceLevel(row["importance_level"]) if row["importance_level"] else None
        ),
        importance_justification=row["importance_justification"],
        summary=row["summary"],
        mentioned_dates=(
            json.loads(row["mentioned_dates"]) if row["mentioned_dates"] is not None else None
        ),
        action_items=(
            json.loads(row["action_items"]) if row["action_items"] is not None else None
        ),
        category=row["category"],
        is_scheduling_related=_flag("is_scheduling_related"),
        calendar_context=_decode_calendar_context(row["calendar_context"]),
        proposed_event=_decode_proposed_event(row["proposed_event"]),
        proposed_event_status=ProposedEventStatus(row["proposed_event_status"]),
        reply_outline=json.loads(row["reply_outline"]) if row["reply_outline"] else None,
        reply_outline_status=ReplyOutlineStatus(row["reply_outline_status"]),
        reply_draft=row["reply_draft"],
        processed_at=(
            datetime.fromisoformat(row["processed_at"]) if row["processed_at"] else None
        ),
        context_processed_at=(
            datetime.fromisoformat(row["context_processed_at"])
            if row["context_processed_at"]
            else None
        ),
    )


# --- public API ------------------------------------------------------------

def upsert(
    emails: Iterable[ProcessedEmail],
    db_path: Optional[Path] = None,
    processed_at: Optional[datetime] = None,
) -> int:
    """Insert or refresh processed records. Returns the number written."""
    rows = [_to_row(e, processed_at) for e in emails]
    if not rows:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(_UPSERT, rows)
        conn.commit()
    return len(rows)


def get(email_id: str, db_path: Optional[Path] = None) -> Optional[ProcessedEmail]:
    with db.connect(db_path) as conn:
        _prepare(conn)
        row = conn.execute(
            "SELECT * FROM processed_email WHERE email_id = ?", (email_id,)
        ).fetchone()
    return _row_to_email(row) if row else None


def all_processed(
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
    order_by_importance: bool = True,
) -> List[ProcessedEmail]:
    """Every processed record, most important (or most recent) first.

    Unscored rows sort last rather than first: SQLite orders NULL before
    everything under DESC, which would put unprocessed mail at the top of the
    review list — the opposite of useful.
    """
    order = (
        "ORDER BY importance_score IS NULL, importance_score DESC, received_at DESC"
        if order_by_importance
        else "ORDER BY received_at DESC"
    )
    sql = "SELECT * FROM processed_email {0}".format(order)
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_email(r) for r in rows]


def count(db_path: Optional[Path] = None) -> int:
    with db.connect(db_path) as conn:
        _prepare(conn)
        return int(conn.execute("SELECT COUNT(*) FROM processed_email").fetchone()[0])


def update_outline(
    email_id: str,
    outline: Optional[List[str]],
    status: ReplyOutlineStatus,
    db_path: Optional[Path] = None,
) -> bool:
    """Save a user-edited outline. Returns False if the email is unknown.

    Used by the review interface, which is the only place an outline's status
    moves to EDITED — the pipeline never sets that.
    """
    with db.connect(db_path) as conn:
        _prepare(conn)
        cursor = conn.execute(
            "UPDATE processed_email SET reply_outline = ?, reply_outline_status = ? "
            "WHERE email_id = ?",
            (
                json.dumps(outline) if outline is not None else None,
                ReplyOutlineStatus(status).value,
                email_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_draft(
    email_id: str,
    draft: str,
    db_path: Optional[Path] = None,
) -> bool:
    """Save a (re)generated full draft. Returns False if the email is unknown.

    Used by the API's manual expand/regenerate endpoint — the pipeline's own
    "expand" stage writes reply_draft as part of the normal upsert instead.
    """
    with db.connect(db_path) as conn:
        _prepare(conn)
        cursor = conn.execute(
            "UPDATE processed_email SET reply_draft = ? WHERE email_id = ?",
            (draft, email_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_proposed_event_status(
    email_id: str,
    status: ProposedEventStatus,
    proposed_event: Optional[ProposedEvent] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Record an approve/decline decision. Returns False if the email is unknown.

    Used by the review interface's approve/decline endpoints, which are the
    only place `proposed_event_status` moves to APPROVED/DECLINED/FAILED —
    the pipeline stops touching a record once it reaches one of those states
    (see pipeline/orchestrate.py's terminal-status guard).

    `proposed_event` is optional so decline can flip just the status; approve
    passes the event back with `google_event_id` (or `error`) filled in by
    `calendaring.events.create_event`.
    """
    with db.connect(db_path) as conn:
        _prepare(conn)
        if proposed_event is not None:
            cursor = conn.execute(
                "UPDATE processed_email SET proposed_event = ?, proposed_event_status = ? "
                "WHERE email_id = ?",
                (
                    _encode_proposed_event(proposed_event),
                    ProposedEventStatus(status).value,
                    email_id,
                ),
            )
        else:
            cursor = conn.execute(
                "UPDATE processed_email SET proposed_event_status = ? WHERE email_id = ?",
                (ProposedEventStatus(status).value, email_id),
            )
        conn.commit()
        return cursor.rowcount > 0
