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
    # Dates/deadlines exactly as stated in the email (no parsing/inference) —
    # None until summarized, [] once summarized if none were mentioned.
    mentioned_dates: Optional[list[str]] = None

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

    # Set once chunk -> embed -> extract (Pipeline.process_context_one) has
    # completed for this email. Read by pipeline/incremental.py to decide
    # whether the context pass is still due — the three stages don't write
    # to any ProcessedEmail field (they write chunk/chunk_vec/mention rows
    # instead), so this is their one completion marker, deliberately not
    # tied to processed_at: a read-status flip must never invalidate it.
    context_processed_at: Optional[datetime] = None


# --- Context graph (Checkpoint 0, PHASES-COMPLEX.md) -----------------------
#
# An entity-centric graph layered on top of the record above: raw_email is
# chunked, embedded, and mined for mentions of people/orgs/cases/projects,
# which resolve to entities and relations, which roll up into per-node
# briefs. Retrieval (BM25 + vector + graph-walk) and the in-app agent read
# this graph; nothing here replaces ProcessedEmail, it's built alongside it.


class EntityKind(str, Enum):
    PERSON = "person"
    ORG = "org"
    CASE = "case"                       # CRM-shaped: ticket / case / incident
    PROJECT = "project"
    DELIVERABLE = "deliverable"
    DOCUMENT = "document"
    TOPIC = "topic"


class ChunkKind(str, Enum):
    BODY = "body"
    QUOTED = "quoted"
    SIGNATURE = "signature"


class MentionSource(str, Enum):
    HEADER = "header"
    REGEX = "regex"
    LLM = "llm"


@dataclass
class Chunk:
    """One paragraph-sized slice of an email's body (Track A,
    context/chunk.py). kind=QUOTED/SIGNATURE chunks are stored but excluded
    from embedding and extraction — see context/chunk.py's quote-stripping
    contract."""

    chunk_id: str
    email_id: str
    ord: int
    text: str
    kind: ChunkKind


@dataclass
class Entity:
    """A resolved person/org/case/project/... node (Track A,
    context/resolve.py). One row per real-world thing, deduplicated across
    however many ways it's mentioned across the corpus."""

    entity_id: str
    kind: EntityKind
    canonical_name: str
    normalized_key: str
    aliases: list[str] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    mention_count: int = 0
    salience: float = 0.0


@dataclass
class Mention:
    """One occurrence of an entity in one email (Track A,
    context/extract.py). chunk_id is None for header-derived mentions, which
    aren't anchored to a specific body chunk."""

    email_id: str
    entity_id: str
    span_text: str
    confidence: float
    source: MentionSource
    chunk_id: Optional[str] = None


@dataclass
class Relation:
    """A directed edge between two entities (Track A,
    context/consolidate.py), evidenced by one or more emails."""

    src_entity_id: str
    dst_entity_id: str
    rel: str                            # belongs_to | participant_in | mentions | owner_of
    weight: float = 0.0
    evidence_email_ids: list[str] = field(default_factory=list)


@dataclass
class Brief:
    """A cached, LLM-written state document for one thread/case/project/
    person node (Track B, retrieval/briefs.py) — "what's happened, who's
    involved, what's still open, what was decided," regenerated only when
    evidence_hash changes."""

    node_type: str                      # thread | case | project | person
    node_id: str
    headline: str
    body_md: str
    open_items: list[str] = field(default_factory=list)
    evidence_email_ids: list[str] = field(default_factory=list)
    evidence_hash: Optional[str] = None
    generated_at: Optional[datetime] = None


@dataclass
class ContextSection:
    """One labeled, provenance-carrying slice of assembled context (Track B,
    retrieval/pack.py). label must read like "From <sender>, <date>, re:
    <subject>" for any foreign (non-anchor) section — see build_pack's
    citation contract."""

    label: str
    text: str
    source_email_ids: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class ContextPack:
    """The one context-assembly output in the codebase (Track B,
    retrieval/pack.py's build_pack — the only function that produces this).
    Consumed by drafting/outline.py, summarization/summarize.py, and
    agent/loop.py; nobody else builds context strings by hand."""

    query: Optional[str] = None
    anchor_email_id: Optional[str] = None
    sections: list[ContextSection] = field(default_factory=list)
    total_chars: int = 0
