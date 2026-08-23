"""The processing pipeline: raw_email in, processed_email out.

Two passes, in this order, and the order is load-bearing:

    context pass    chunk -> embed -> extract          (per email, CONTEXT_STAGES)
    reasoning pass  classify -> score -> summarize -> categorize
                    -> scheduling gate -> calendar -> propose_event -> outline

The context pass must finish for the WHOLE corpus before the reasoning pass
runs for ANY email. Extraction builds the entity graph the reasoning stages
retrieve from; if the two were interleaved per email, email #1's outline would
be generated against a graph that only knows about email #1, while email #160's
would see everything — the correlation the graph exists to provide would be
available to the last message in a run and absent from the first. Hence two
entry points (`run_context` then `process`) rather than one longer stage list,
and hence CONTEXT_STAGES is deliberately NOT appended to STAGES.

Within the reasoning pass, stage order is fixed and each stage is skippable,
because the stages are not independent — the reply-outline gate reads
`is_no_reply`, and the calendar stage only runs when the scheduling gate says so.

Every stage is wrapped: one email that fails classification must not abort a
100-email run. A stage failure leaves its field None, which is exactly what
"not processed yet" looks like, so the next run retries it.

This module deliberately contains no LLM prompts and no SQL. It sequences
functions the other tracks own, so changing a prompt or a table never means
editing the orchestrator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field, replace
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from models.schema import (
    Chunk,
    ChunkKind,
    Mention,
    ProcessedEmail,
    ProposedEventStatus,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)

log = logging.getLogger(__name__)

# The context pass. Separate from STAGES on purpose — see the module docstring:
# these must complete corpus-wide before any reasoning stage runs, so they are
# a different pass, not three more entries in the same list.
CONTEXT_STAGES: Sequence[str] = ("chunk", "embed", "extract")

# Stage names, in run order. Used by the CLI's --only/--skip flags and by
# incremental.py to name the stages a change invalidates.
STAGES: Sequence[str] = (
    "classify",
    "score",
    "summarize",
    "action_items",
    "categorize",
    "scheduling",
    "calendar",
    "propose_event",
    "outline",
)

# Every stage name the pipeline knows, context pass first. For CLI validation
# and error messages; nothing iterates this to run stages, because the two
# passes have separate entry points.
ALL_STAGE_NAMES: Sequence[str] = tuple(CONTEXT_STAGES) + tuple(STAGES)


# Once a proposed event has been acted on, a re-run must never regenerate or
# overwrite it — APPROVED may already carry a live google_event_id, and
# DECLINED is a recorded user decision, not a value the pipeline owns.
_TERMINAL_EVENT_STATUSES = (ProposedEventStatus.APPROVED, ProposedEventStatus.DECLINED)


class StageError(Exception):
    """A single stage failed for a single email. Never fatal to a run."""


@dataclass(frozen=True)
class ContextResult:
    """What the context pass derived from one email.

    Returned rather than written, for the same reason `process` returns
    ProcessedEmail records rather than persisting them: this module sequences
    functions the other tracks own and contains no SQL. The caller hands these
    to `context.store` — which is also what lets the whole pass be tested with
    no database.
    """

    email_id: str
    chunks: List[Chunk] = dc_field(default_factory=list)
    # (chunk_id, float32 little-endian blob) for the BODY chunks only.
    vectors: List[Tuple[str, bytes]] = dc_field(default_factory=list)
    mentions: List[Mention] = dc_field(default_factory=list)

    @property
    def body_chunks(self) -> List[Chunk]:
        return [c for c in self.chunks if c.kind == ChunkKind.BODY]


def to_processed(raw: RawEmail) -> ProcessedEmail:
    """A fresh ProcessedEmail carrying only the fields copied from RawEmail."""
    return ProcessedEmail(
        email_id=raw.email_id,
        thread_id=raw.thread_id,
        sender=raw.sender,
        subject=raw.subject,
        received_at=raw.received_at,
        read_status=ReadStatus(raw.read_status),
    )


class Pipeline:
    """Sequences the per-email stages.

    Dependencies are injected rather than imported at call time so tests can
    run the whole pipeline with no API keys, no network, and no LLM — and so
    a caller can disable an expensive stage without the module knowing how
    that stage is implemented.
    """

    def __init__(
        self,
        classify: Optional[Callable] = None,
        score: Optional[Callable] = None,
        summarize: Optional[Callable] = None,
        action_items: Optional[Callable] = None,
        categorize: Optional[Callable] = None,
        scheduling_gate: Optional[Callable] = None,
        calendar_context: Optional[Callable] = None,
        propose_event: Optional[Callable] = None,
        outline: Optional[Callable] = None,
        chunk: Optional[Callable] = None,
        embed: Optional[Callable] = None,
        extract: Optional[Callable] = None,
        stages: Optional[Sequence[str]] = None,
        calendar_window_days: int = 7,
    ):
        self._chunk = chunk
        self._embed = embed
        self._extract = extract
        self._classify = classify
        self._score = score
        self._summarize = summarize
        self._action_items = action_items
        self._categorize = categorize
        self._scheduling_gate = scheduling_gate
        self._calendar_context = calendar_context
        self._propose_event = propose_event
        self._outline = outline
        self.stages = tuple(stages if stages is not None else STAGES)
        self.calendar_window_days = calendar_window_days
        self.errors: List[str] = []

    # --- defaults ----------------------------------------------------------

    @classmethod
    def with_defaults(cls, stages: Optional[Sequence[str]] = None, **kwargs) -> "Pipeline":
        """Wire up the real implementations from every track.

        Imports are deferred to here so that importing this module — which the
        API does — never pulls in the Anthropic SDK or the Google client
        libraries just to read a stored row.
        """
        from calendaring.context import get_calendar_context
        from calendaring.propose import extract_proposed_event
        from calendaring.scheduling_intent import is_scheduling_related
        from classification.categorize import categorize_topic
        from classification.classify import is_no_reply
        from drafting.outline import generate_reply_outline
        from scoring.score import score_importance
        from summarization.action_items import extract_action_items
        from summarization.summarize import summarize as summarize_one

        # The context-pass imports are gated on the stage list, not just
        # deferred: a reasoning-only run (the default, and every existing
        # caller) must not pay to import numpy and the embeddings client, and
        # must not fail because the context package is unavailable.
        wanted = set(stages if stages is not None else STAGES)
        context_kwargs = (
            cls._context_defaults() if wanted & set(CONTEXT_STAGES) else {}
        )

        return cls(
            **context_kwargs,
            classify=is_no_reply,
            score=score_importance,
            summarize=summarize_one,
            action_items=extract_action_items,
            categorize=categorize_topic,
            scheduling_gate=is_scheduling_related,
            calendar_context=get_calendar_context,
            propose_event=extract_proposed_event,
            outline=generate_reply_outline,
            stages=stages,
            **kwargs,
        )

    @staticmethod
    def _context_defaults() -> Dict[str, Callable]:
        """The real context-pass implementations, imported on demand."""
        from context.chunk import chunk_email
        from context.embed import embed_chunks
        from context.extract import extract_entities

        return {"chunk": chunk_email, "embed": embed_chunks, "extract": extract_entities}

    # --- stage plumbing ----------------------------------------------------

    def _run_stage(self, name: str, email_id: str, fn: Callable, *args, **kwargs):
        """Run one stage, converting any failure into a logged None.

        Graceful degradation over crashing (Phase 8's rule, applied from the
        start): a summarization timeout on message 47 should not cost the
        other 99 messages their processing.
        """
        if name not in self.stages or fn is None:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            message = "{0} failed for {1}: {2}".format(name, email_id, exc)
            log.warning(message)
            self.errors.append(message)
            return None

    # --- the context pass --------------------------------------------------

    def run_context_one(self, raw: RawEmail) -> ContextResult:
        """Chunk, embed, and extract one email. No DB writes, no ProcessedEmail.

        Chunking always runs when any context stage is enabled, even if only
        "embed" or "extract" is due: it is pure string work with no network and
        no model, and both of the expensive stages take chunks as input. Only
        the two costly stages (an HTTP round trip and an LLM call) are gated,
        which is where the savings actually are.
        """
        if not set(self.stages) & set(CONTEXT_STAGES):
            return ContextResult(email_id=raw.email_id)

        chunks = self._run_stage("chunk", raw.email_id, self._chunk, raw) or []
        result = ContextResult(email_id=raw.email_id, chunks=list(chunks))
        body = result.body_chunks

        # Only BODY chunks are embedded or mined. Quoted reply history would
        # make every message in a thread near-identical in vector space, and
        # would credit the quoted author's entities to whoever replied.
        vectors = self._run_stage("embed", raw.email_id, self._embed, body) or []
        mentions = self._run_stage("extract", raw.email_id, self._extract, raw, body) or []
        return replace(result, vectors=list(vectors), mentions=list(mentions))

    def run_context(
        self,
        emails: Iterable[RawEmail],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[ContextResult]:
        """The whole context pass over a batch. Run this to completion — and
        then consolidate — before calling `process` on anything."""
        emails = list(emails)
        self.errors = []
        results: List[ContextResult] = []
        for index, raw in enumerate(emails, start=1):
            results.append(self.run_context_one(raw))
            if on_progress:
                on_progress(index, len(emails))
        return results

    # --- the pipeline ------------------------------------------------------

    def process_one(
        self,
        raw: RawEmail,
        existing: Optional[ProcessedEmail] = None,
        now: Optional[datetime] = None,
    ) -> ProcessedEmail:
        """Run the enabled stages for one email.

        `existing` carries forward results from a previous run so a partial
        re-run (say, only `outline`) keeps the summary and score it already
        had instead of blanking them.
        """
        record = replace(existing) if existing is not None else to_processed(raw)
        # Read status is the one field that must always be refreshed: it is
        # what makes an email become eligible for an outline.
        record.read_status = ReadStatus(raw.read_status)

        classified = self._run_stage("classify", raw.email_id, self._classify, raw)
        if classified is not None:
            record.is_no_reply, record.no_reply_reason = classified

        scored = self._run_stage(
            "score", raw.email_id, self._score, raw, bool(record.is_no_reply)
        )
        if scored is not None:
            (
                record.importance_score,
                record.importance_level,
                record.importance_justification,
            ) = scored

        summarized = self._run_stage("summarize", raw.email_id, self._summarize, raw)
        if summarized is not None:
            record.summary, record.mentioned_dates = summarized

        items = self._run_stage("action_items", raw.email_id, self._action_items, raw)
        if items is not None:
            record.action_items = items

        topic = self._run_stage("categorize", raw.email_id, self._categorize, raw)
        if topic is not None:
            record.category = topic

        gate = self._run_stage("scheduling", raw.email_id, self._scheduling_gate, raw)
        if gate is not None:
            record.is_scheduling_related = bool(gate)

        # The gate exists to avoid paying for this call on every email.
        if record.is_scheduling_related and "calendar" in self.stages:
            context = self._run_stage(
                "calendar",
                raw.email_id,
                self._fetch_calendar,
                now=now,
            )
            if context is not None:
                record.calendar_context = context

        if (
            record.is_scheduling_related
            and "propose_event" in self.stages
            and record.proposed_event_status not in _TERMINAL_EVENT_STATUSES
        ):
            proposed = self._run_stage(
                "propose_event", raw.email_id, self._propose_event, record, raw
            )
            if proposed is not None:
                record.proposed_event, record.proposed_event_status = proposed

        outlined = self._run_stage(
            "outline", raw.email_id, self._outline, record, raw
        )
        if outlined is not None:
            record.reply_outline, record.reply_outline_status = outlined
        elif "outline" in self.stages and self._outline is not None:
            # The stage ran and failed. Leave the outline unset but make the
            # gate's verdict visible, so the UI can distinguish "not eligible"
            # from "eligible, generation failed".
            from drafting.outline import is_eligible

            _, status = is_eligible(record)
            if status != ReplyOutlineStatus.SUGGESTED:
                record.reply_outline_status = status

        record.processed_at = now or datetime.now(timezone.utc)
        return record

    def _fetch_calendar(self, now: Optional[datetime] = None):
        """One calendar window shared by every scheduling email in a run."""
        if self._cached_calendar is not None:
            return self._cached_calendar
        from datetime import timedelta

        start = now or datetime.now(timezone.utc)
        self._cached_calendar = self._calendar_context(
            start, start + timedelta(days=self.calendar_window_days)
        )
        return self._cached_calendar

    _cached_calendar = None

    def process(
        self,
        emails: Iterable[RawEmail],
        existing: Optional[Dict[str, ProcessedEmail]] = None,
        now: Optional[datetime] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[ProcessedEmail]:
        """Run the pipeline over a batch, reusing one calendar window."""
        emails = list(emails)
        existing = existing or {}
        self.errors = []
        self._cached_calendar = None

        results: List[ProcessedEmail] = []
        for index, raw in enumerate(emails, start=1):
            results.append(
                self.process_one(raw, existing.get(raw.email_id), now=now)
            )
            if on_progress:
                on_progress(index, len(emails))
        return results
