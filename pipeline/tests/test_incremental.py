"""Tests for reprocess_on_read_status_change(): must only touch the one
email whose read_status changed, leaving every other persisted row (and
its outline) untouched - not a full-inbox reprocess.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from drafting import outline as outline_module
from ingestion.models import RawEmail as IngestedRawEmail
from ingestion.store import upsert_emails
from models.schema import ProcessedEmail, ReadStatus, ReplyOutlineStatus
from pipeline.incremental import reprocess_on_read_status_change
from pipeline.persist import get_processed_email, upsert_processed_email


def make_ingested(email_id: str, read_status: str) -> IngestedRawEmail:
    return IngestedRawEmail(
        email_id=email_id,
        thread_id=f"thread-{email_id}",
        sender="alex@example.com",
        subject="Quick sync this week?",
        body_text="Hey, do you have time for a quick sync this week?",
        snippet="Hey, do you have time...",
        received_at="2026-08-26T09:00:00+00:00",
        read_status=read_status,
        headers={"To": "me@example.com"},
    )


def make_processed(email_id: str, read_status: ReadStatus) -> ProcessedEmail:
    return ProcessedEmail(
        email_id=email_id,
        thread_id=f"thread-{email_id}",
        sender="alex@example.com",
        subject="Quick sync this week?",
        received_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        read_status=read_status,
        is_no_reply=False,
        importance_score=50.0,
        summary="Alex wants a sync.",
        is_scheduling_related=False,
        reply_outline=None,
        reply_outline_status=ReplyOutlineStatus.NONE,
    )


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(outline_module, "_call_llm", lambda prompt: calls.append(prompt) or ["Acknowledge the request"])
    return calls


def test_only_the_changed_email_is_reprocessed(tmp_path, stub_llm):
    raw_db = tmp_path / "raw.db"
    processed_db = tmp_path / "processed.db"

    upsert_emails(
        [make_ingested("email-1", "unread"), make_ingested("email-2", "unread")],
        db_path=raw_db,
    )
    upsert_processed_email(make_processed("email-1", ReadStatus.UNREAD), db_path=processed_db)
    upsert_processed_email(make_processed("email-2", ReadStatus.UNREAD), db_path=processed_db)

    result = reprocess_on_read_status_change(
        "email-1", ReadStatus.READ, db_path=processed_db, raw_db_path=raw_db
    )

    assert result.read_status == ReadStatus.READ
    assert result.reply_outline == ["Acknowledge the request"]
    assert result.reply_outline_status == ReplyOutlineStatus.SUGGESTED
    assert len(stub_llm) == 1  # only one email's outline was (re)generated

    email_1 = get_processed_email("email-1", db_path=processed_db)
    assert email_1.read_status == ReadStatus.READ
    assert email_1.reply_outline == ["Acknowledge the request"]

    email_2 = get_processed_email("email-2", db_path=processed_db)
    assert email_2.read_status == ReadStatus.UNREAD
    assert email_2.reply_outline is None
    assert email_2.reply_outline_status == ReplyOutlineStatus.NONE


def test_summary_is_not_regenerated_when_already_present(tmp_path, stub_llm):
    raw_db = tmp_path / "raw.db"
    processed_db = tmp_path / "processed.db"

    upsert_emails([make_ingested("email-1", "unread")], db_path=raw_db)
    upsert_processed_email(make_processed("email-1", ReadStatus.UNREAD), db_path=processed_db)

    result = reprocess_on_read_status_change(
        "email-1", ReadStatus.READ, db_path=processed_db, raw_db_path=raw_db
    )

    assert result.summary == "Alex wants a sync."  # unchanged, was already set


def test_raises_when_email_was_never_processed(tmp_path):
    with pytest.raises(KeyError):
        reprocess_on_read_status_change(
            "missing-email", ReadStatus.READ, db_path=tmp_path / "processed.db", raw_db_path=tmp_path / "raw.db"
        )
