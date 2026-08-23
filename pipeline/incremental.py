"""Deciding what actually needs reprocessing.

Reprocessing 100 emails because one was marked read costs ~100x more in LLM
calls than it should. This module answers two questions: does this email need
work at all, and if so, which stages.

The interesting case is the read-status flip. When an unread email becomes
read it becomes eligible for a reply outline — but its summary, score, and
classification are all still valid, because none of them depend on read
status. So the correct response is to re-run `outline` alone.
"""

from __future__ import annotations

from typing import AbstractSet, Dict, List, Mapping, Optional, Sequence, Tuple

from models.schema import ProcessedEmail, ProposedEventStatus, RawEmail, ReadStatus

from .orchestrate import ALL_STAGE_NAMES, CONTEXT_STAGES

# Which stage fills which field. A stage is due when its field is still unset.
_STAGE_OUTPUT = {
    "classify": "is_no_reply",
    "score": "importance_score",
    "summarize": "summary",
    "action_items": "action_items",
    "categorize": "category",
    "scheduling": "is_scheduling_related",
    # chunk/embed/extract (the context pass) don't write to a ProcessedEmail
    # field each — they write chunk/chunk_vec/mention rows instead — so all
    # three share one completion marker rather than three independent ones.
    # There's no partial state where chunks exist but extraction doesn't
    # that this module needs to reason about; process_context_one runs them
    # as one atomic per-email unit.
    "chunk": "context_processed_at",
    "embed": "context_processed_at",
    "extract": "context_processed_at",
}


# The context pass needs its own map, because a context stage's output is not
# a ProcessedEmail field — it is rows in a side table. `_STAGE_OUTPUT` above
# answers "is this stage due?" by reading an attribute off the record; there is
# no attribute to read for chunks, vectors, or mentions, and inventing one on
# the shared schema to make the lookup uniform would be a fiction: nothing
# would ever read it, and it would have to be kept in sync with row counts by
# hand. So the question is answered against the tables instead, and this module
# stays pure by taking the answer as an argument (see `context.store`'s
# coverage query) rather than opening a connection itself. Public rather than
# underscored because `context.store.context_coverage` reads it to keep the two
# halves of the map from drifting — that makes it contract, not detail.
CONTEXT_STAGE_TABLE = {
    "chunk": "chunk",
    "embed": "chunk_vec",
    "extract": "mention",
}

# {stage: {email_id, ...}} — the emails that already have rows for that stage.
ContextCoverage = Mapping[str, AbstractSet[str]]


def context_stages_for(email_id: str, coverage: ContextCoverage) -> Tuple[str, ...]:
    """The context stages still owed for one email.

    Deliberately blind to read status. A read-status flip changes eligibility
    for a reply outline; it does not change one character of the body, so
    re-chunking, re-embedding, and re-extracting would be paid work for a
    guaranteed-identical result. `stages_for` handles the flip by re-running
    "outline" alone, and this function must not undo that thrift.
    """
    return tuple(
        stage
        for stage in CONTEXT_STAGES
        if email_id not in (coverage.get(stage) or frozenset())
    )


def context_plan(
    raws: Sequence[RawEmail],
    coverage: ContextCoverage,
) -> Dict[str, Tuple[str, ...]]:
    """{email_id: context stages} for every email whose context is incomplete."""
    out: Dict[str, Tuple[str, ...]] = {}
    for raw in raws:
        stages = context_stages_for(raw.email_id, coverage)
        if stages:
            out[raw.email_id] = stages
    return out


def stages_for(
    raw: RawEmail,
    existing: Optional[ProcessedEmail],
) -> Tuple[str, ...]:
    """The stages that need to run for this email.

    Returns () when the record is complete and unchanged — the caller skips
    the email entirely, making a no-op re-run cost zero LLM calls.
    """
    if existing is None or existing.processed_at is None:
        return tuple(ALL_STAGE_NAMES)

    due: List[str] = [
        stage
        for stage, field in _STAGE_OUTPUT.items()
        if getattr(existing, field) is None
    ]

    if existing.summary is not None and existing.mentioned_dates is None:
        # A row summarized before mentioned_dates existed — back-fill it
        # rather than treating "summary is set" as "fully summarized".
        due.append("summarize")

    read_flipped = ReadStatus(raw.read_status) != ReadStatus(existing.read_status)
    if read_flipped:
        # Only eligibility changed. Classification, score, and summary are
        # unaffected by whether the user has opened the message — and
        # neither is the context graph: the body didn't change, so
        # chunk/embed/extract must NOT be appended here, only "outline".
        due.append("outline")
    elif _outline_missing(existing):
        due.append("outline")

    if "outline" in due or _draft_missing(existing):
        due.append("expand")

    if "scheduling" in due or (existing.is_scheduling_related and existing.calendar_context is None):
        due.append("calendar")

    # A row processed before this feature shipped has proposed_event_status
    # NONE with no calendar re-fetch pending — without this clause it would
    # never become due, since none of the checks above would fire for it.
    if "calendar" in due or (
        existing.is_scheduling_related
        and existing.proposed_event_status == ProposedEventStatus.NONE
    ):
        due.append("propose_event")

    # Preserve canonical stage order (context pass before reasoning pass);
    # the two-pass architecture depends on it.
    return tuple(stage for stage in ALL_STAGE_NAMES if stage in set(due))


def _outline_missing(existing: ProcessedEmail) -> bool:
    """Whether an eligible email is still missing its outline.

    Deliberately does not re-run for an email the gate excluded — an unread or
    no-reply email has no outline *by design*, and treating that as missing
    would make every run retry every no-reply email forever.
    """
    from drafting.outline import is_eligible

    eligible, _ = is_eligible(existing)
    return eligible and not existing.reply_outline


def _draft_missing(existing: ProcessedEmail) -> bool:
    """Whether an eligible email has an outline but still needs its draft.

    Mirrors _outline_missing: an unread/no-reply email has no outline (and
    so no draft) by design, and re-running "expand" for it forever would be
    the same bug _outline_missing avoids.
    """
    from drafting.outline import is_eligible

    eligible, _ = is_eligible(existing)
    return eligible and bool(existing.reply_outline) and not existing.reply_draft


def plan(
    raws: Sequence[RawEmail],
    existing: Dict[str, ProcessedEmail],
) -> Dict[str, Tuple[str, ...]]:
    """{email_id: stages} for every email that needs work. Skips the rest."""
    out: Dict[str, Tuple[str, ...]] = {}
    for raw in raws:
        stages = stages_for(raw, existing.get(raw.email_id))
        if stages:
            out[raw.email_id] = stages
    return out


def summarize_plan(plan_map: Dict[str, Tuple[str, ...]], total: int) -> str:
    """A one-line human summary of what a run is about to do.

    Handles a context plan as well as a reasoning plan — the stage names are
    disjoint, so ordering against both lists keeps either kind readable.
    """
    if not plan_map:
        return "Nothing to do: all {0} emails are up to date.".format(total)
    counts: Dict[str, int] = {}
    for stages in plan_map.values():
        for stage in stages:
            counts[stage] = counts.get(stage, 0) + 1
    detail = ", ".join(
        "{0}x{1}".format(stage, counts[stage])
        for stage in ALL_STAGE_NAMES
        if stage in counts
    )
    return "{0}/{1} emails need work ({2})".format(len(plan_map), total, detail)
