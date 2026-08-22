"""Shared data model contract for the AI email agent.

Owned by nobody. Frozen after Phase 0 — changes require flagging
Track A/B/C before editing.
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
    NONE = "none"                      # unread — not eligible yet
    NOT_APPLICABLE = "not_applicable"  # no-reply — never eligible
    SUGGESTED = "suggested"            # generated, untouched
    EDITED = "edited"                  # user modified it
    EXPANDED_TO_DRAFT = "expanded_to_draft"
    SENT = "sent"


@dataclass
class RawEmail:
    """Ingestion output (Track A) — one row from Gmail, pre-classification."""
    email_id: str
    thread_id: str
    sender: str
    subject: str
    body_text: str
    received_at: datetime
    read_status: ReadStatus
    headers: dict[str, str] = field(default_factory=dict)
    # headers of interest: List-Unsubscribe, Precedence, Auto-Submitted, Content-Type


@dataclass
class CalendarSlot:
    start: datetime
    end: datetime


@dataclass
class CalendarContext:
    """Calendar output (Track A) — populated only when is_scheduling_related is True."""
    checked_range_start: datetime
    checked_range_end: datetime
    busy_blocks: list[CalendarSlot] = field(default_factory=list)
    suggested_slots: list[CalendarSlot] = field(default_factory=list)


@dataclass
class ProcessedEmail:
    """The pipeline's end product — one row per email."""

    # identity / ingestion (Track A)
    email_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime
    read_status: ReadStatus

    # classification (Track B)
    is_no_reply: bool
    no_reply_reason: Optional[str] = None

    # scoring (Track B)
    importance_score: Optional[ImportanceLevel] = None
    importance_justification: Optional[str] = None

    # summarization (Track B)
    summary: Optional[str] = None  # 1-3 sentences

    # calendar (Track A, gated by scheduling-intent check)
    is_scheduling_related: bool = False
    calendar_context: Optional[CalendarContext] = None

    # drafting (Track C)
    # INVARIANT: reply_outline is non-None only when
    # read_status == ReadStatus.READ and is_no_reply is False.
    reply_outline: Optional[list[str]] = None
    reply_outline_status: ReplyOutlineStatus = ReplyOutlineStatus.NONE
