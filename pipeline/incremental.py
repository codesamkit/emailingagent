"""Incremental re-run (Phase 6, step 3): when a single email's read_status
changes, only re-run what actually depends on it - reply-outline
generation always (the read/no-reply gate is keyed on read_status), and
summarization only if it's missing - never a full inbox reprocess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from drafting.outline import generate_reply_outline
from ingestion.store import get as get_raw_email
from models.schema import ProcessedEmail, ReadStatus
from summarization.summarize import summarize

from .adapt import adapt_raw_email
from .persist import get_processed_email, upsert_processed_email


def reprocess_on_read_status_change(
    email_id: str,
    new_read_status: ReadStatus,
    *,
    db_path: Optional[Path] = None,
    raw_db_path: Optional[Path] = None,
    summarization_client: Optional[Any] = None,
) -> ProcessedEmail:
    """Update one persisted email's read_status and re-run only the steps
    that depend on it, then re-persist just that row.

    Raises KeyError if the email has never been processed, or if its
    original raw row is no longer in ingestion's raw_email table (needed
    to rebuild the prompt for outline/summary regeneration).
    """
    processed = get_processed_email(email_id, db_path=db_path)
    if processed is None:
        raise KeyError(f"No processed_email row for email_id={email_id!r} - run process_inbox first")

    ingested_raw = get_raw_email(email_id, db_path=raw_db_path)
    if ingested_raw is None:
        raise KeyError(
            f"No raw_email row for email_id={email_id!r} - it may have been purged from ingestion's store"
        )
    raw = adapt_raw_email(ingested_raw)

    processed.read_status = new_read_status
    raw.read_status = new_read_status  # keep in sync for the outline gate

    if processed.summary is None:
        processed.summary = summarize(raw, client=summarization_client)

    processed.reply_outline, processed.reply_outline_status = generate_reply_outline(processed, raw)

    upsert_processed_email(processed, db_path=db_path)
    return processed
