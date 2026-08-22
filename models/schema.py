"""Shared data contract — frozen after Phase 0.

Owned by nobody; changes require flagging Track A, B, and C first
(see CONTEXT.md's integration checkpoint rules).

- Track A (ingestion/calendar) produces RawEmail, CalendarContext, and
  ProposedEvent (never written to Calendar without explicit user approval).
- Track B (classification/scoring/summarization) fills in ProcessedEmail's
  is_no_reply, importance_score/level, and summary fields.
- Track C (drafting/pipeline) fills in reply_outline and orchestrates
  persistence via models/db.py.

All `datetime` fields in this contract are timezone-aware UTC. Ingestion
derives `received_at` from Gmail's `internalDate` (an absolute UTC instant),
and mixing naive and aware datetimes raises `TypeError` on comparison — so
producers must attach a timezone and consumers may rely on one being there.

Plain dataclasses (stdlib only) are used deliberately: the tech stack
(FastAPI vs. something else) isn't finalized yet, so this contract doesn't
assume a web framework. If FastAPI is confirmed later, these can be wrapped
in or ported to Pydantic models without changing field names/shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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


class ProposedEventStatus(str, Enum):
    NONE = "none"                       # not scheduling-related, or nothing extractable
    SUGGESTED = "suggested"             # extracted, awaiting user decision
    APPROVED = "approved"               # user approved; event exists on Google Calendar
    DECLINED = "declined"               # user declined; never created
    FAILED = "failed"                   # user approved but the Calendar API call failed


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
    # Timezone-aware UTC, matching every other datetime in this contract.
    # datetime.utcnow() returns a *naive* value, which cannot be compared or
    # subtracted against the aware datetimes ingestion produces — the same
    # defect that made scoring/signals.py raise TypeError on real mail.
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class ProposedEvent:
    """A meeting extracted from a scheduling-related email, awaiting the
    user's approve/decline decision. Never written to Google Calendar until
    an explicit user action calls calendaring/events.py:create_event — see
    interfaces/README.md."""

    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)
    location: Optional[str] = None
    description: Optional[str] = None
    # Set once an APPROVED event has actually been created.
    google_event_id: Optional[str] = None
    # Set when status is FAILED — the Calendar API call raised.
    error: Optional[str] = None


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

    # Track B — topic categorization (classification/categorize.py).
    # A short free-form lowercase topic (1-3 words, e.g. "job application",
    # "order shipping"), normalized in code. None until the stage has run.
    category: Optional[str] = None

    # Track A — scheduling gate + calendar (calendar/scheduling_intent.py, calendar/context.py)
    is_scheduling_related: Optional[bool] = None
    calendar_context: Optional[CalendarContext] = None

    # Track A — proposed event extraction (calendaring/propose.py), gated on
    # is_scheduling_related. Only ever written to Calendar via an explicit
    # user approval (calendaring/events.py) — never automatically.
    proposed_event: Optional[ProposedEvent] = None
    proposed_event_status: ProposedEventStatus = ProposedEventStatus.NONE

    # Track C — drafting (drafting/outline.py)
    reply_outline: Optional[list[str]] = None
    reply_outline_status: ReplyOutlineStatus = ReplyOutlineStatus.NONE

    # Pipeline bookkeeping (pipeline/orchestrate.py) — set each time this
    # record is (re)processed; supports incremental re-run and debugging.
    processed_at: Optional[datetime] = None
