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

from . import incremental, persist
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
        "errors": [],
    }
    if dry_run:
        return result

    if not plan_map:
        # Nothing to reprocess, but the spread pass still runs so score
        # distribution stays even after upgrades or manual row edits.
        result["respread"] = apply_score_spread(db_path)
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
    result["respread"] = apply_score_spread(db_path)
    result["totalStored"] = persist.count(db_path)
    return result


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
    if stages:
        pipeline = Pipeline.with_defaults(stages=stages)
        persist.upsert(pipeline.process([raw], existing=existing), db_path)
    return persist.get(email_id, db_path)


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
