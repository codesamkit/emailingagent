"""One-call refresh: ingest new mail, process what changed, respread scores.

Shared by the pipeline CLI and the API's POST /api/refresh so the terminal
and the Gmail extension run exactly the same code path instead of two
diverging copies of the orchestration loop.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ingestion import config as ingest_config
from ingestion import store

from . import incremental, persist, todo
from .orchestrate import STAGES, Pipeline

# on_progress(stages, done, total) — stages identifies the batch being run.
ProgressFn = Callable[[Tuple[str, ...], int, int], None]


class RefreshBusyError(RuntimeError):
    """A refresh is already running; concurrent runs would double-process."""


def apply_score_spread(db_path: Optional[Path] = None) -> int:
    """Even out importance scores within each level band across the whole
    inbox (scoring/spread.py). Per-email scoring clusters — similar emails
    have similar signals — so separation is applied corpus-wide after the
    fact. Cheap (no LLM), deterministic, idempotent."""
    from scoring.spread import spread_scores

    rows = persist.all_processed(db_path)
    before = {e.email_id: e.importance_score for e in rows}
    changed = [e for e in spread_scores(rows) if before[e.email_id] != e.importance_score]
    if changed:
        persist.upsert(changed, db_path)
    return len(changed)


def sync_todos(db_path: Optional[Path] = None) -> int:
    """Refresh the derived to-do list (pipeline/todo.py) against every
    processed email. Cheap (no LLM), reads the whole table like the spread
    pass, so it's run once at the end of a batch rather than per-email."""
    return todo.sync(persist.all_processed(db_path), db_path)


def apply_feedback_priors(db_path: Optional[Path] = None) -> int:
    """Apply the user's stored sender corrections (feedback/) to every
    processed row. Runs before the spread pass so a pinned level gets its
    even within-band placement like any other score. Cheap (no LLM),
    deterministic, idempotent."""
    from feedback.apply import apply_feedback

    changed = apply_feedback(persist.all_processed(db_path), db_path=db_path)
    if changed:
        persist.upsert(changed, db_path)
    return len(changed)


def ingest_new(
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
    query: Optional[str] = None,
) -> int:
    """Fetch recent messages from Gmail and upsert into raw_email.

    Non-interactive on purpose: when called from the API there is no terminal
    to complete an OAuth flow in, so a missing/expired token raises instead
    of hanging on a browser prompt.
    """
    from ingestion.fetch import fetch_recent_emails
    from ingestion.gmail_auth import get_gmail_service

    service = get_gmail_service(allow_interactive=False)
    emails = list(
        fetch_recent_emails(
            service,
            limit=limit or ingest_config.DEFAULT_LIMIT,
            query=query or ingest_config.DEFAULT_QUERY,
        )
    )
    store.init_db(db_path)
    return store.upsert_emails(emails, db_path)


def process_incremental(
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
    force_all: bool = False,
    skip: Sequence[str] = (),
    dry_run: bool = False,
    on_progress: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    """Plan and run the pipeline over stored raw emails.

    Returns a summary dict; with dry_run=True the plan is built but nothing
    executes. Raises nothing on stage failures — they are collected in
    `errors` and the affected fields stay unset for the next run.
    """
    raws = store.recent(limit or 10_000, db_path)
    existing = {e.email_id: e for e in persist.all_processed(db_path)}

    if force_all:
        plan_map: Dict[str, Tuple[str, ...]] = {r.email_id: tuple(STAGES) for r in raws}
    else:
        plan_map = incremental.plan(raws, existing)

    if skip:
        plan_map = {
            eid: tuple(s for s in stages if s not in skip)
            for eid, stages in plan_map.items()
        }
        plan_map = {eid: s for eid, s in plan_map.items() if s}

    result: Dict[str, Any] = {
        "totalRaw": len(raws),
        "planned": len(plan_map),
        "plan": plan_map,
        "summary": incremental.summarize_plan(plan_map, len(raws)),
        "written": 0,
        "respread": 0,
        "feedbackApplied": 0,
        "todosSynced": 0,
        "errors": [],
    }
    if dry_run:
        return result

    if not plan_map:
        # Nothing to reprocess, but feedback + spread still run so user
        # corrections land and score distribution stays even; the to-do
        # list also needs a pass so a manually-sent reply (outside this app)
        # still closes its needs_reply row.
        result["feedbackApplied"] = apply_feedback_priors(db_path)
        result["respread"] = apply_score_spread(db_path)
        result["todosSynced"] = sync_todos(db_path)
        return result

    todo = [r for r in raws if r.email_id in plan_map]
    # One pipeline per distinct stage set keeps each email's disabled stages
    # genuinely disabled rather than filtered after the fact.
    by_stages: Dict[Tuple[str, ...], List] = {}
    for raw in todo:
        by_stages.setdefault(plan_map[raw.email_id], []).append(raw)

    written = 0
    errors: List[str] = []
    for stages, batch in by_stages.items():
        pipeline = Pipeline.with_defaults(stages=stages)
        progress = (
            (lambda done, total, _s=stages: on_progress(_s, done, total))
            if on_progress
            else None
        )
        results = pipeline.process(batch, existing=existing, on_progress=progress)
        written += persist.upsert(results, db_path)
        errors.extend(pipeline.errors)

    result["written"] = written
    result["errors"] = errors
    # User corrections have the last word over whatever the model just said;
    # then the spread pass evens out the final ordering.
    result["feedbackApplied"] = apply_feedback_priors(db_path)
    result["respread"] = apply_score_spread(db_path)
    result["todosSynced"] = sync_todos(db_path)
    result["totalStored"] = persist.count(db_path)
    return result


def rescore_sender(sender: str, db_path: Optional[Path] = None) -> int:
    """Re-run the scoring stage for every stored email from one sender.

    Needed after a feedback prior is cleared. `feedback/apply.py` overwrites
    importance_level/score/justification in place, and the model's original
    verdict is not kept anywhere, so forgetting a prior cannot simply restore
    what was there — the level has to be recomputed. Scoped to the one
    sender so an undo costs a handful of LLM calls, not a full reprocess.

    Returns the number of emails re-scored.
    """
    from scoring.signals import _addr_only

    wanted = _addr_only(sender)
    existing = {e.email_id: e for e in persist.all_processed(db_path)}
    raws = [
        raw
        for raw in store.recent(10_000, db_path)
        if _addr_only(raw.sender) == wanted
    ]
    if not raws:
        return 0

    pipeline = Pipeline.with_defaults(stages=("score",))
    persist.upsert(pipeline.process(raws, existing=existing), db_path)
    return len(raws)


def refresh_one(email_id: str, db_path: Optional[Path] = None):
    """Re-fetch a single message from Gmail and run whatever stages it needs.

    The fast path behind the extension's open-email view: when the user opens
    a message, Gmail flips it to read, and this picks up the flip (unlocking
    the reply outline) in seconds instead of waiting for the next full
    refresh. Also handles brand-new mail the bulk ingest hasn't seen.

    Raises googleapiclient.errors.HttpError (404) for an unknown message id
    and RuntimeError when Gmail auth is unavailable non-interactively.
    """
    from ingestion.fetch import get_message
    from ingestion.gmail_auth import get_gmail_service
    from ingestion.parse import to_raw_email

    service = get_gmail_service(allow_interactive=False)
    raw = to_raw_email(get_message(service, email_id))
    store.init_db(db_path)
    store.upsert_emails([raw], db_path)

    existing = {e.email_id: e for e in persist.all_processed(db_path)}
    stages = incremental.stages_for(raw, existing.get(email_id))
    # Keep this call fast: it runs synchronously while the user is staring at
    # "Generating reply outline..." right after opening a previously-unread
    # email. Full-draft expansion is a second, separate LLM call on top of
    # the outline's own — doubling this live wait for a draft the user isn't
    # looking at yet. The extension's periodic /api/refresh poll (every 2
    # minutes, see extension/content/api.js's PIPELINE_REFRESH_MS) picks up
    # the missing draft in the background instead; the on-demand /expand
    # endpoint still covers the rare case someone opens the outline before
    # that poll runs.
    stages = tuple(s for s in stages if s != "expand")
    if stages:
        pipeline = Pipeline.with_defaults(stages=stages)
        persist.upsert(pipeline.process([raw], existing=existing), db_path)
    updated = persist.get(email_id, db_path)
    if updated is not None:
        todo.sync([updated], db_path)
    return updated


_refresh_lock = threading.Lock()


def refresh(
    db_path: Optional[Path] = None,
    ingest_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Ingest then process, guarded so concurrent callers don't double-run.

    Raises RefreshBusyError when a refresh is already in flight — the caller
    should treat that as "in progress", not a failure.
    """
    if not _refresh_lock.acquire(blocking=False):
        raise RefreshBusyError("A refresh is already running.")
    try:
        ingested = ingest_new(db_path, limit=ingest_limit)
        result = process_incremental(db_path)
        result["ingested"] = ingested
        return result
    finally:
        _refresh_lock.release()
