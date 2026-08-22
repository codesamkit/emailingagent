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

from typing import Dict, List, Optional, Sequence, Tuple

from models.schema import ProcessedEmail, RawEmail, ReadStatus

from .orchestrate import STAGES

# Which stage fills which field. A stage is due when its field is still unset.
_STAGE_OUTPUT = {
    "classify": "is_no_reply",
    "score": "importance_score",
    "summarize": "summary",
    "scheduling": "is_scheduling_related",
}


def stages_for(
    raw: RawEmail,
    existing: Optional[ProcessedEmail],
) -> Tuple[str, ...]:
    """The stages that need to run for this email.

    Returns () when the record is complete and unchanged — the caller skips
    the email entirely, making a no-op re-run cost zero LLM calls.
    """
    if existing is None or existing.processed_at is None:
        return tuple(STAGES)

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
        # unaffected by whether the user has opened the message.
        due.append("outline")
    elif _outline_missing(existing):
        due.append("outline")

    if "scheduling" in due or (existing.is_scheduling_related and existing.calendar_context is None):
        due.append("calendar")

    # Preserve canonical stage order; the pipeline depends on it.
    return tuple(stage for stage in STAGES if stage in set(due))


def _outline_missing(existing: ProcessedEmail) -> bool:
    """Whether an eligible email is still missing its outline.

    Deliberately does not re-run for an email the gate excluded — an unread or
    no-reply email has no outline *by design*, and treating that as missing
    would make every run retry every no-reply email forever.
    """
    from drafting.outline import is_eligible

    eligible, _ = is_eligible(existing)
    return eligible and not existing.reply_outline


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
    """A one-line human summary of what a run is about to do."""
    if not plan_map:
        return "Nothing to do: all {0} emails are up to date.".format(total)
    counts: Dict[str, int] = {}
    for stages in plan_map.values():
        for stage in stages:
            counts[stage] = counts.get(stage, 0) + 1
    detail = ", ".join(
        "{0}x{1}".format(stage, counts[stage]) for stage in STAGES if stage in counts
    )
    return "{0}/{1} emails need work ({2})".format(len(plan_map), total, detail)
