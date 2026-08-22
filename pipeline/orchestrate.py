"""End-to-end orchestration pipeline (Track C, Phase 6).

Wires every other track's real module together for one email: ingest ->
no-reply detection -> importance scoring -> summarization -> calendar
context (if scheduling-related) -> reply outline (if eligible). Import
only - no other track's internals are edited here.

process_email() is also what incremental.py calls (with a single email)
when a read_status change needs a partial re-run, so it stays a pure
function of one ingested email in, one ProcessedEmail out - no hidden
dependency on having just run the full inbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from calendaring.context import get_calendar_context
from calendaring.scheduling_intent import is_scheduling_related
from calendaring.suggest import suggest_for_context
from classification.classify import is_no_reply as classify_no_reply
from drafting.outline import generate_reply_outline
from ingestion.models import RawEmail as IngestedRawEmail
from models.schema import ProcessedEmail
from scoring.score import score_importance
from summarization.summarize import summarize

from .adapt import adapt_raw_email

# How far ahead to check the calendar, and the slot length to propose, for
# scheduling-related emails. No per-email signal for meeting length exists
# yet, so a flat default is used - matches calendar/context.py's own
# DEFAULT_LOOKAHEAD_DAYS window for consistency.
CALENDAR_LOOKAHEAD_DAYS = 14
SUGGESTED_SLOT_DURATION_MINUTES = 30


def process_email(
    ingested: IngestedRawEmail,
    *,
    calendar_service: Optional[Any] = None,
    scoring_client: Optional[Any] = None,
    summarization_client: Optional[Any] = None,
) -> ProcessedEmail:
    """Run one email through the full pipeline and return its ProcessedEmail.

    calendar_service/scoring_client/summarization_client are threaded
    through purely so callers (incremental.py, tests) can inject fakes
    instead of hitting real APIs; classification has no client seam of
    its own to accept (see classification/classify.py).
    """
    raw = adapt_raw_email(ingested)

    no_reply, no_reply_reason = classify_no_reply(raw)
    score, level, justification = score_importance(raw, no_reply, client=scoring_client)
    summary = summarize(raw, client=summarization_client)

    processed = ProcessedEmail(
        email_id=raw.email_id,
        thread_id=raw.thread_id,
        sender=raw.sender,
        subject=raw.subject,
        received_at=raw.received_at,
        read_status=raw.read_status,
        is_no_reply=no_reply,
        no_reply_reason=no_reply_reason,
        importance_score=score,
        importance_level=level,
        importance_justification=justification,
        summary=summary,
    )

    processed.is_scheduling_related = is_scheduling_related(raw)
    if processed.is_scheduling_related:
        processed.calendar_context = _build_calendar_context(raw.received_at, calendar_service)

    processed.reply_outline, processed.reply_outline_status = generate_reply_outline(processed, raw)
    processed.processed_at = datetime.now(timezone.utc)

    return processed


def _build_calendar_context(anchor: datetime, calendar_service: Optional[Any]):
    range_start = anchor
    range_end = anchor + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
    calendar_context = get_calendar_context(range_start, range_end, service=calendar_service)
    return suggest_for_context(calendar_context, SUGGESTED_SLOT_DURATION_MINUTES, service=calendar_service)


def process_inbox(
    limit: int = 50,
    *,
    gmail_service: Optional[Any] = None,
    calendar_service: Optional[Any] = None,
    raw_db_path: Optional[Any] = None,
    processed_db_path: Optional[Any] = None,
) -> list[ProcessedEmail]:
    """Fetch the `limit` most recent emails (Track A's ingestion), persist
    the raw rows (so incremental.py can look them up later), run each
    through process_email, and persist the results (Phase 6, step 2).
    Returns the processed emails, most important first.
    """
    from ingestion.fetch import fetch_recent_emails
    from ingestion.store import upsert_emails

    from .persist import upsert_processed_email

    raw_emails = list(fetch_recent_emails(gmail_service, limit=limit))
    upsert_emails(raw_emails, db_path=raw_db_path)

    processed_emails = []
    for raw in raw_emails:
        processed = process_email(raw, calendar_service=calendar_service)
        upsert_processed_email(processed, db_path=processed_db_path)
        processed_emails.append(processed)

    return sorted(
        processed_emails,
        key=lambda p: p.importance_score if p.importance_score is not None else -1.0,
        reverse=True,
    )
