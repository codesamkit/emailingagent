"""Shared data contract — frozen after Phase 0.

Owned by nobody; changes require flagging Track A, B, and C first
(see CONTEXT.md's integration checkpoint rules).

- Track A (ingestion/calendar) produces RawEmail and CalendarContext.
- Track B (classification/scoring/summarization) fills in ProcessedEmail's
  is_no_reply, importance_score/level, and summary fields.
- Track C (drafting/pipeline) fills in reply_outline and orchestrates
  persistence via models/db.py.

Plain dataclasses (stdlib only) are used deliberately: the tech stack
(FastAPI vs. something else) isn't finalized yet, so this contract doesn't
assume a web framework. If FastAPI is confirmed later, these can be wrapped
in or ported to Pydantic models without changing field names/shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ReadStatus(str, Enum):
    READ = "read"
    UNREAD = "unread"


class ImportanceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ReplyOutlineStatus(str, Enum):
    NONE = "none"                       # unread — not eligible yet, no LLM call made
    NOT_APPLICABLE = "not_applicable"   # no-reply — never eligible, no LLM call made
    SUGGESTED = "suggested"             # generated, not yet touched by the user
    EDITED = "edited"                   # user edited the generated outline
    EXPANDED_TO_DRAFT = "expanded_to_draft"
    SENT = "sent"


@dataclass
class RawEmail:
    """Output of ingestion (Track A). One row per Gmail message."""

    email_id: str
    thread_id: str
    sender: str
    recipients: list[str]
    subject: str
    body: str                       # plain text, HTML stripped
    received_at: datetime
    read_status: ReadStatus
    # Raw headers needed downstream for no-reply detection (Track B) and
    # scheduling detection (Track A/calendar): List-Unsubscribe,
    # Precedence, Auto-Submitted, etc. Keys are header names as-is from Gmail.
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class CalendarSlot:
    start: datetime
    end: datetime


@dataclass
class CalendarContext:
    """Output of the calendar module (Track A). Populated on a
    ProcessedEmail only when is_scheduling_related == True — see
    interfaces/README.md for the is_scheduling_related contract."""

    range_start: datetime
    range_end: datetime
    busy_blocks: list[CalendarSlot] = field(default_factory=list)
    existing_events: list[dict] = field(default_factory=list)
    suggested_slots: list[CalendarSlot] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProcessedEmail:
    """Shared output record, filled in incrementally as an email moves
    through classification -> scoring -> summarization -> calendar ->
    drafting. Optional fields start as None and are populated by the
    pipeline stage that owns them (see interfaces/README.md)."""

    # Copied through from RawEmail, not re-derived
    email_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime
    read_status: ReadStatus

    # Track B — classification (classification/classify.py)
    is_no_reply: Optional[bool] = None
    no_reply_reason: Optional[str] = None

    # Track B — scoring (scoring/score.py)
    importance_score: Optional[float] = None          # normalized 0-100
    importance_level: Optional[ImportanceLevel] = None
    importance_justification: Optional[str] = None

    # Track B — summarization (summarization/summarize.py)
    summary: Optional[str] = None

    # Track A — scheduling gate + calendar (calendar/scheduling_intent.py, calendar/context.py)
    is_scheduling_related: Optional[bool] = None
    calendar_context: Optional[CalendarContext] = None

    # Track C — drafting (drafting/outline.py)
    reply_outline: Optional[list[str]] = None
    reply_outline_status: ReplyOutlineStatus = ReplyOutlineStatus.NONE

    # Pipeline bookkeeping (pipeline/orchestrate.py) — set each time this
    # record is (re)processed; supports incremental re-run and debugging.
    processed_at: Optional[datetime] = None
