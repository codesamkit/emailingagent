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

    # Track B — action-item extraction (summarization/action_items.py).
    # Concrete tasks/asks the email requires of the recipient, distinct from
    # `summary` (which deliberately omits inferred action items) and from
    # `reply_outline` (only ever set for read, non-no-reply emails — this
    # field is extracted for every email, so a no-reply notification with a
    # real deadline still surfaces one). None until extracted, [] once
    # extracted if the email asked nothing of the recipient.
    action_items: Optional[list[str]] = None

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


# ---------------------------------------------------------------------------
# Context graph (post-Phase 8, PHASES-COMPLEX.md Checkpoint 0)
#
# Appended, never edited: every dataclass above already has rows in a shipped
# database and callers in three tracks. Everything below describes the
# retrieval substrate — chunks, entities, the edges between them, and the
# cached briefs that summarize a subgraph — plus the char-budgeted
# ContextPack that is the only shape an LLM prompt ever sees context in.
#
# These are frozen: unlike ProcessedEmail (which the pipeline fills in field
# by field as stages run) a Chunk or a Mention is derived wholly in one pass
# and then only ever replaced. Use dataclasses.replace to evolve one.
# ---------------------------------------------------------------------------


class EntityKind(str, Enum):
    """What kind of thing a graph node is.

    CASE is the CRM-shaped unit of work — a ticket, case, or incident, the
    thing an ID like "SUP-4471" names. It is deliberately distinct from
    PROJECT (the longer-lived container a case belongs to) because the whole
    premise is correlating separate threads that share one case or project.
    """

    PERSON = "person"
    ORG = "org"
    CASE = "case"
    PROJECT = "project"
    DELIVERABLE = "deliverable"
    DOCUMENT = "document"
    TOPIC = "topic"


class ChunkKind(str, Enum):
    """Which part of a message a chunk came from.

    Only BODY is embedded and mined for entities. QUOTED and SIGNATURE are
    stored rather than discarded so nothing is silently lost, but including
    them would make every message in a thread embed near-identically and
    would attribute the quoted author's entities to the replier.
    """

    BODY = "body"
    QUOTED = "quoted"
    SIGNATURE = "signature"


class MentionSource(str, Enum):
    """Which extraction pass produced a mention.

    Kept on the row so `context.cli email <id>` can show which pass found
    what — a regex mention being wrong is a different fix from the model
    hallucinating one.
    """

    HEADER = "header"
    REGEX = "regex"
    LLM = "llm"


class RelationKind(str, Enum):
    """The edge types in the graph.

    A closed set rather than free strings: `context.consolidate` writes these
    and `retrieval.search`'s graph walk reads them, and a typo in one place
    would silently drop a whole channel's worth of edges.
    """

    BELONGS_TO = "belongs_to"          # case -> project
    PARTICIPANT_IN = "participant_in"  # person -> case | project
    MENTIONS = "mentions"              # symmetric catch-all co-occurrence
    OWNER_OF = "owner_of"              # person -> deliverable | document


class BriefNodeType(str, Enum):
    """What a rollup brief summarizes.

    THREAD keys on RawEmail.thread_id; the other three key on an entity_id.
    """

    THREAD = "thread"
    CASE = "case"
    PROJECT = "project"
    PERSON = "person"


@dataclass(frozen=True)
class Chunk:
    """One embeddable span of one email's text.

    `ord` is the position within the email (0-based) so chunks can be put
    back in reading order without consulting offsets.
    """

    chunk_id: str
    email_id: str
    ord: int
    text: str
    kind: ChunkKind = ChunkKind.BODY


@dataclass(frozen=True)
class Entity:
    """A resolved graph node — one real-world thing, however many ways it
    was written.

    `normalized_key` is the deterministic identity used for exact matching
    and is unique per (kind, key): for PERSON it is the bare email address,
    never the display name, because the same human arrives as "Sam",
    "Sam Shah", and "S. Shah" in different messages. `aliases` records every
    surface form seen, which is what lets a later mention match without an
    embedding comparison.
    """

    entity_id: str
    kind: EntityKind
    canonical_name: str
    normalized_key: str
    aliases: list[str] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    mention_count: int = 0
    # How central this node is to the corpus, 0-1. Used to rank the graph
    # retrieval channel; a node mentioned once should not outrank a project
    # that runs through forty emails.
    salience: float = 0.0


@dataclass(frozen=True)
class Mention:
    """One occurrence of one entity in one email.

    `chunk_id` is None for mentions that come from headers or the subject
    line, which belong to the message rather than to any body chunk.
    """

    email_id: str
    entity_id: str
    span_text: str
    chunk_id: Optional[str] = None
    confidence: float = 1.0
    source: MentionSource = MentionSource.REGEX


@dataclass(frozen=True)
class Relation:
    """A weighted, evidenced edge between two entities.

    `evidence_email_ids` is what makes an edge auditable — a relation with
    one supporting email is a coincidence, and the consolidation pass uses
    the count to decide whether an edge is real.
    """

    src_entity_id: str
    dst_entity_id: str
    rel: RelationKind
    weight: float = 1.0
    evidence_email_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Brief:
    """A cached, LLM-written state document for one node of the rollup tree.

    This is the "working memory" of the design: not a digest of the emails
    underneath (we already have per-email summaries, and concatenating them
    is worthless) but what has happened, who is involved, what is still open,
    and what was decided.

    `evidence_hash` is the cache key — a hash over the sorted evidence
    email_ids and their processed_at values. Unchanged hash means the brief
    is still true, and regenerating it would be a paid call for no new
    information.
    """

    node_type: BriefNodeType
    node_id: str
    headline: str
    body_md: str
    open_items: list[str] = field(default_factory=list)
    evidence_email_ids: list[str] = field(default_factory=list)
    evidence_hash: str = ""
    generated_at: Optional[datetime] = None


@dataclass(frozen=True)
class ContextSection:
    """One labelled, attributed block inside a ContextPack.

    `label` carries provenance ("From <sender>, <date>, re: <subject>")
    because without it a model blurs facts from several emails together
    instead of citing them, and a wrong claim can't be traced to its source.
    """

    label: str
    text: str
    source_email_ids: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass(frozen=True)
class ContextPack:
    """Everything an LLM call is allowed to know beyond the email itself.

    The single currency of the retrieval layer: outline generation,
    context-aware summarization, and the agent's search tool all consume
    this one shape, so there is exactly one place where a char budget is
    enforced and one place where provenance is attached.
    """

    query: Optional[str] = None
    anchor_email_id: Optional[str] = None
    sections: list[ContextSection] = field(default_factory=list)
    total_chars: int = 0


# --- To-do list (pipeline/todo.py) ------------------------------------------
#
# A derived, user-checkable list layered on top of ProcessedEmail — not a
# new track output. Two kinds of row: one per string in `action_items`, and
# one "needs a reply" marker per email that is not no-reply and hasn't been
# replied to yet. See pipeline/todo.py's module docstring for the stable-id
# scheme that lets a completed item survive the next pipeline re-run.


class TodoKind(str, Enum):
    ACTION_ITEM = "action_item"
    NEEDS_REPLY = "needs_reply"


class TodoStatus(str, Enum):
    OPEN = "open"
    DONE = "done"


@dataclass
class TodoItem:
    """One row of the derived to-do list. `todo_id` is a stable hash of
    (email_id, kind, text) — see pipeline/todo.py:make_todo_id — not a
    random id, so re-deriving the same item on a later pipeline run
    recognizes it instead of minting a duplicate open row."""

    todo_id: str
    email_id: str
    kind: TodoKind
    text: str
    status: TodoStatus = TodoStatus.OPEN
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
