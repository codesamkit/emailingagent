"""Correctness-critical gating tests for reply-outline generation.

Asserts:
  - unread emails never get an outline (and never hit the LLM)
  - no-reply emails never get one, even if manually marked read
  - only read + non-no-reply emails get one
  - scheduling emails fold calendar-derived slots into the outline
  - flipping read_status from unread to read regenerates the outline

Uses local mock ProcessedEmail/RawEmail/CalendarContext fixtures rather
than Track A/B's real fixtures.json, since it doesn't exist yet.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from drafting import outline as outline_module
from drafting.outline import generate_reply_outline
from models.schema import (
    CalendarContext,
    CalendarSlot,
    ProcessedEmail,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)


def make_processed(**overrides) -> ProcessedEmail:
    defaults = dict(
        email_id="email-1",
        thread_id="thread-1",
        sender="alex@example.com",
        subject="Quick sync this week?",
        received_at=datetime.now(timezone.utc),
        read_status=ReadStatus.READ,
        is_no_reply=False,
        summary="Alex wants to schedule a quick sync call.",
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


def make_raw(**overrides) -> RawEmail:
    defaults = dict(
        email_id="email-1",
        thread_id="thread-1",
        sender="alex@example.com",
        recipients=["me@example.com"],
        subject="Quick sync this week?",
        body="Hey, do you have time for a quick sync this week?",
        received_at=datetime.now(timezone.utc),
        read_status=ReadStatus.READ,
    )
    defaults.update(overrides)
    return RawEmail(**defaults)


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    calls: list[str] = []

    def fake_call_llm(prompt: str) -> list[str]:
        calls.append(prompt)
        return ["Acknowledge the request", "Ask if they need the doc beforehand"]

    monkeypatch.setattr(outline_module, "_call_llm", fake_call_llm)
    return calls


def test_unread_email_never_gets_an_outline(mock_llm):
    processed = make_processed(read_status=ReadStatus.UNREAD, is_no_reply=False)
    raw = make_raw(read_status=ReadStatus.UNREAD)

    outline, status = generate_reply_outline(processed, raw)

    assert outline is None
    assert status == ReplyOutlineStatus.NONE
    assert mock_llm == []  # LLM never invoked


def test_no_reply_email_never_gets_an_outline_even_when_read(mock_llm):
    processed = make_processed(read_status=ReadStatus.READ, is_no_reply=True)
    raw = make_raw(read_status=ReadStatus.READ)

    outline, status = generate_reply_outline(processed, raw)

    assert outline is None
    assert status == ReplyOutlineStatus.NOT_APPLICABLE
    assert mock_llm == []


def test_no_reply_email_never_gets_an_outline_when_unread(mock_llm):
    processed = make_processed(read_status=ReadStatus.UNREAD, is_no_reply=True)
    raw = make_raw(read_status=ReadStatus.UNREAD)

    outline, status = generate_reply_outline(processed, raw)

    assert outline is None
    assert status == ReplyOutlineStatus.NOT_APPLICABLE
    assert mock_llm == []


def test_read_and_not_no_reply_gets_an_outline(mock_llm):
    processed = make_processed(read_status=ReadStatus.READ, is_no_reply=False)
    raw = make_raw(read_status=ReadStatus.READ)

    outline, status = generate_reply_outline(processed, raw)

    assert outline == [
        "Acknowledge the request",
        "Ask if they need the doc beforehand",
    ]
    assert status == ReplyOutlineStatus.SUGGESTED
    assert len(mock_llm) == 1


def test_scheduling_email_folds_calendar_slots_into_outline(mock_llm):
    now = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)  # a Wednesday
    calendar_context = CalendarContext(
        range_start=now,
        range_end=now + timedelta(days=7),
        busy_blocks=[],
        suggested_slots=[
            CalendarSlot(start=now.replace(hour=14), end=now.replace(hour=14, minute=30)),
            CalendarSlot(
                start=now.replace(day=27, hour=10),
                end=now.replace(day=27, hour=10, minute=30),
            ),
        ],
    )
    processed = make_processed(
        read_status=ReadStatus.READ,
        is_no_reply=False,
        is_scheduling_related=True,
        calendar_context=calendar_context,
    )
    raw = make_raw(read_status=ReadStatus.READ)

    outline, status = generate_reply_outline(processed, raw)

    assert status == ReplyOutlineStatus.SUGGESTED
    assert len(outline) == 3  # 2 LLM bullets + 1 calendar bullet
    slot_bullet = outline[-1]
    assert "Wed 2pm" in slot_bullet
    assert "Thu 10am" in slot_bullet


def test_regeneration_on_read_status_flip(mock_llm):
    processed = make_processed(read_status=ReadStatus.UNREAD, is_no_reply=False)
    raw = make_raw(read_status=ReadStatus.UNREAD)

    outline, status = generate_reply_outline(processed, raw)
    assert outline is None
    assert status == ReplyOutlineStatus.NONE
    assert mock_llm == []

    processed.read_status = ReadStatus.READ
    raw.read_status = ReadStatus.READ
    outline, status = generate_reply_outline(processed, raw)

    assert outline is not None
    assert status == ReplyOutlineStatus.SUGGESTED
    assert len(mock_llm) == 1
