"""Tests for process_email(): verifies the full track-wiring order and
field mapping using fixtures/monkeypatched upstream functions rather than
real APIs. Correctness of each track's own logic is that track's
responsibility (their own test suites) - this only checks that pipeline
wires their outputs into ProcessedEmail correctly and respects the
drafting gate end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from drafting import outline as outline_module
from ingestion.models import RawEmail as IngestedRawEmail
from models.schema import CalendarContext, CalendarSlot, ImportanceLevel, ReplyOutlineStatus
from pipeline import orchestrate as orchestrate_module
from pipeline.orchestrate import process_email, process_inbox


def make_ingested(**overrides) -> IngestedRawEmail:
    defaults = dict(
        email_id="email-1",
        thread_id="thread-1",
        sender="alex@example.com",
        subject="Quick sync this week?",
        body_text="Hey, do you have time for a quick sync this week?",
        snippet="Hey, do you have time...",
        received_at="2026-08-26T09:00:00+00:00",
        read_status="read",
        label_ids=["INBOX"],
        headers={"To": "me@example.com"},
        has_attachments=False,
    )
    defaults.update(overrides)
    return IngestedRawEmail(**defaults)


@pytest.fixture(autouse=True)
def stub_upstream(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "classify_no_reply", lambda email: (False, "personal sender"))
    monkeypatch.setattr(
        orchestrate_module,
        "score_importance",
        lambda email, is_no_reply, client=None: (80.0, ImportanceLevel.HIGH, "looks important"),
    )
    monkeypatch.setattr(orchestrate_module, "summarize", lambda email, client=None: "Alex wants a sync.")
    monkeypatch.setattr(outline_module, "_call_llm", lambda prompt: ["Acknowledge the request"])


def test_process_email_wires_all_tracks_for_a_non_scheduling_email(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: False)

    processed = process_email(make_ingested())

    assert processed.email_id == "email-1"
    assert processed.is_no_reply is False
    assert processed.importance_score == 80.0
    assert processed.importance_level == ImportanceLevel.HIGH
    assert processed.summary == "Alex wants a sync."
    assert processed.is_scheduling_related is False
    assert processed.calendar_context is None
    assert processed.reply_outline == ["Acknowledge the request"]
    assert processed.reply_outline_status == ReplyOutlineStatus.SUGGESTED
    assert processed.processed_at is not None


def test_process_email_folds_calendar_slots_for_scheduling_email(monkeypatch):
    now = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
    fake_context = CalendarContext(range_start=now, range_end=now, busy_blocks=[])

    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: True)
    monkeypatch.setattr(
        orchestrate_module,
        "get_calendar_context",
        lambda range_start, range_end, service=None: fake_context,
    )
    def fake_suggest_for_context(context, duration_minutes=None, **kwargs):
        context.suggested_slots = [
            CalendarSlot(start=now.replace(hour=14), end=now.replace(hour=14, minute=30)),
        ]
        return context

    monkeypatch.setattr(orchestrate_module, "suggest_for_context", fake_suggest_for_context)

    processed = process_email(make_ingested())

    assert processed.is_scheduling_related is True
    assert processed.calendar_context is fake_context
    assert processed.calendar_context.suggested_slots  # populated by suggest_for_context
    assert len(processed.reply_outline) == 2  # 1 LLM bullet + 1 calendar bullet
    assert "Wed 2pm" in processed.reply_outline[-1]


def test_process_email_never_calls_llm_for_unread_email(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: False)
    llm_calls = []
    monkeypatch.setattr(outline_module, "_call_llm", lambda prompt: llm_calls.append(prompt) or [])

    processed = process_email(make_ingested(read_status="unread"))

    assert processed.reply_outline is None
    assert processed.reply_outline_status == ReplyOutlineStatus.NONE
    assert llm_calls == []


def test_process_email_never_gets_outline_for_no_reply_even_when_read(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "classify_no_reply", lambda email: (True, "no-reply@ sender"))
    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: False)
    llm_calls = []
    monkeypatch.setattr(outline_module, "_call_llm", lambda prompt: llm_calls.append(prompt) or [])

    processed = process_email(make_ingested(read_status="read"))

    assert processed.is_no_reply is True
    assert processed.reply_outline is None
    assert processed.reply_outline_status == ReplyOutlineStatus.NOT_APPLICABLE
    assert llm_calls == []


# --- Phase 8: graceful degradation on API failures --------------------


def test_scoring_failure_degrades_to_unscored_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: False)

    def flaky_score_importance(email, is_no_reply, client=None):
        raise RuntimeError("scoring API down")

    monkeypatch.setattr(orchestrate_module, "score_importance", flaky_score_importance)

    processed = process_email(make_ingested())

    assert processed.importance_score is None
    assert processed.importance_level is None
    # everything downstream of scoring still completes normally
    assert processed.summary == "Alex wants a sync."
    assert processed.reply_outline == ["Acknowledge the request"]


def test_summarization_failure_degrades_to_pending_placeholder(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: False)

    def flaky_summarize(email, client=None):
        raise RuntimeError("summarization API down")

    monkeypatch.setattr(orchestrate_module, "summarize", flaky_summarize)

    processed = process_email(make_ingested())

    assert processed.summary == "(summary pending)"
    assert processed.importance_score == 80.0  # unaffected
    assert processed.reply_outline == ["Acknowledge the request"]  # outline still generates


def test_calendar_context_failure_degrades_to_no_slot_suggestions(monkeypatch):
    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: True)

    def flaky_get_calendar_context(range_start, range_end, service=None):
        raise RuntimeError("Calendar API rate limited")

    monkeypatch.setattr(orchestrate_module, "get_calendar_context", flaky_get_calendar_context)

    processed = process_email(make_ingested())

    assert processed.is_scheduling_related is True
    assert processed.calendar_context is None
    # outline still generates, just without a calendar-derived slot bullet
    assert processed.reply_outline == ["Acknowledge the request"]


def test_process_inbox_skips_one_failing_email_without_aborting_the_batch(monkeypatch, tmp_path):
    import ingestion.fetch as ingestion_fetch_module
    import ingestion.store as ingestion_store_module
    import pipeline.persist as persist_module

    good = make_ingested(email_id="good-1")
    bad = make_ingested(email_id="bad-1")

    monkeypatch.setattr(orchestrate_module, "is_scheduling_related", lambda email: False)

    def flaky_classify(email):
        if email.email_id == "bad-1":
            raise RuntimeError("classification service unavailable")
        return False, "personal sender"

    monkeypatch.setattr(orchestrate_module, "classify_no_reply", flaky_classify)
    monkeypatch.setattr(
        ingestion_fetch_module, "fetch_recent_emails", lambda service, limit: iter([good, bad])
    )
    monkeypatch.setattr(ingestion_store_module, "upsert_emails", lambda emails, db_path=None: None)

    persisted = []
    monkeypatch.setattr(
        persist_module, "upsert_processed_email", lambda processed, db_path=None: persisted.append(processed)
    )

    result = process_inbox(limit=2, processed_db_path=tmp_path / "processed.db", raw_db_path=tmp_path / "raw.db")

    assert [p.email_id for p in result] == ["good-1"]
    assert [p.email_id for p in persisted] == ["good-1"]
