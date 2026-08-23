"""The processing pipeline: raw_email in, processed_email out.

Stage order is fixed and each stage is skippable, because the stages are not
independent — the reply-outline gate reads `is_no_reply`, and the calendar
stage only runs when the scheduling gate says so:

    classify -> score -> summarize -> categorize -> scheduling gate -> calendar -> outline -> expand

Every stage is wrapped: one email that fails classification must not abort a
100-email run. A stage failure leaves its field None, which is exactly what
"not processed yet" looks like, so the next run retries it.

This module deliberately contains no LLM prompts and no SQL. It sequences
functions the other tracks own, so changing a prompt or a table never means
editing the orchestrator.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from models.schema import (
    ChunkKind,
    ProcessedEmail,
    ProposedEventStatus,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)

log = logging.getLogger(__name__)

# Stage names, in run order. Used by the CLI's --only/--skip flags and by
# incremental.py to name the stages a change invalidates.
STAGES: Sequence[str] = (
    "classify",
    "score",
    "summarize",
    "categorize",
    "scheduling",
    "calendar",
    "propose_event",
    "outline",
    "expand",
)

# The context-graph pass (PHASES-COMPLEX.md Checkpoint 0): chunk -> embed ->
# extract, run per email but BEFORE any reasoning stage, and for the WHOLE
# corpus before any single email's reasoning stages run (see
# process_context_one's docstring for why). Kept as its own tuple, not
# folded into STAGES, since these stages produce chunks/vectors/mentions for
# the context graph, not ProcessedEmail fields.
CONTEXT_STAGES: Sequence[str] = ("chunk", "embed", "extract")

ALL_STAGE_NAMES: Sequence[str] = tuple(CONTEXT_STAGES) + tuple(STAGES)

# Once a proposed event has been acted on, a re-run must never regenerate or
# overwrite it — APPROVED may already carry a live google_event_id, and
# DECLINED is a recorded user decision, not a value the pipeline owns.
_TERMINAL_EVENT_STATUSES = (ProposedEventStatus.APPROVED, ProposedEventStatus.DECLINED)


class StageError(Exception):
    """A single stage failed for a single email. Never fatal to a run."""


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
        categorize: Optional[Callable] = None,
        scheduling_gate: Optional[Callable] = None,
        calendar_context: Optional[Callable] = None,
        propose_event: Optional[Callable] = None,
        outline: Optional[Callable] = None,
        expand: Optional[Callable] = None,
        chunk: Optional[Callable] = None,
        embed: Optional[Callable] = None,
        extract: Optional[Callable] = None,
        stages: Optional[Sequence[str]] = None,
        calendar_window_days: int = 7,
    ):
        self._classify = classify
        self._score = score
        self._summarize = summarize
        self._categorize = categorize
        self._scheduling_gate = scheduling_gate
        self._calendar_context = calendar_context
        self._propose_event = propose_event
        self._outline = outline
        self._expand = expand
        self._chunk = chunk
        self._embed = embed
        self._extract = extract
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
        from drafting.expand import expand_outline_to_full_draft
        from drafting.outline import generate_reply_outline
        from scoring.score import score_importance
        from summarization.summarize import summarize as summarize_one

        # The context-graph stages (Track A: context/chunk.py, llm/embeddings.py,
        # context/extract.py) don't exist until Track A's branch builds them.
        # Importing them unconditionally would break with_defaults() for every
        # caller -- including the 8 reasoning stages above, which have nothing
        # to do with Track A -- until that package lands. Degrade to an unwired
        # (None) stage instead, same as _run_stage already does for any stage
        # whose callable is None: this starts working with zero further change
        # to this file the moment context/chunk.py etc. exist.
        try:
            from context.chunk import chunk_email as chunk_fn
        except ImportError:
            chunk_fn = None
        try:
            from llm.embeddings import embed_texts as embed_fn
        except ImportError:
            embed_fn = None
        try:
            from context.extract import extract_entities as extract_fn
        except ImportError:
            extract_fn = None

        return cls(
            classify=is_no_reply,
            score=score_importance,
            summarize=summarize_one,
            categorize=categorize_topic,
            scheduling_gate=is_scheduling_related,
            calendar_context=get_calendar_context,
            propose_event=extract_proposed_event,
            outline=generate_reply_outline,
            expand=expand_outline_to_full_draft,
            chunk=chunk_fn,
            embed=embed_fn,
            extract=extract_fn,
            stages=stages,
            **kwargs,
        )

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

    # --- context-graph pass --------------------------------------------------

    def process_context_one(self, raw: RawEmail) -> tuple:
        """Run chunk -> embed -> extract for one email.

        Separate from process_one: these stages produce chunks/vectors/
        mentions for the context graph (context/store.py persists them),
        not ProcessedEmail fields. Two-pass ordering (PHASES-COMPLEX.md
        section 2) requires the WHOLE corpus's context pass to finish, and
        consolidate() to run, before ANY email's reasoning pass (classify
        through outline) starts -- otherwise email #1's outline is built
        against a graph that doesn't have email #160's entities in it yet.
        Driving that corpus-wide two-pass order is pipeline/refresh.py's
        job, not this method's -- this just runs the three stages for one
        email, through the same _run_stage isolation every other stage
        gets, so a chunking failure on email 47 doesn't cost the other 159.

        Only kind=BODY chunks are embedded/extracted -- quoted text and
        signatures are stored (by context/store.py) but poison embeddings
        and misattribute entities if included (see context/chunk.py).
        """
        chunks = self._run_stage("chunk", raw.email_id, self._chunk, raw) or []
        body_chunks = [c for c in chunks if getattr(c, "kind", None) == ChunkKind.BODY]

        vectors: list = []
        if body_chunks:
            body_texts = [c.text for c in body_chunks]
            vectors = self._run_stage("embed", raw.email_id, self._embed, body_texts) or []

        mentions = (
            self._run_stage("extract", raw.email_id, self._extract, raw, body_chunks) or []
        )

        return chunks, vectors, mentions

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

        # Full-draft expansion rides the outline's own eligibility gate — it
        # only runs once, the first time an outline is suggested. A draft
        # already on the record (from a prior run, or a user-triggered
        # expand) is never overwritten here; regenerating one is a manual
        # action (the API's /expand endpoint), not something a routine
        # pipeline re-run should silently clobber.
        if (
            record.reply_outline_status == ReplyOutlineStatus.SUGGESTED
            and record.reply_outline
            and not record.reply_draft
        ):
            draft = self._run_stage(
                "expand", raw.email_id, self._expand, record.email_id, outline=record.reply_outline
            )
            if draft is not None:
                record.reply_draft = draft

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
