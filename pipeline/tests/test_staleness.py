"""Tests for find_stale_outlines() (Phase 8, Track C item 3)."""

from __future__ import annotations

from datetime import datetime, timezone

from models.schema import ProcessedEmail, ReadStatus, ReplyOutlineStatus
from pipeline.staleness import find_stale_outlines

_NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


def make_processed(email_id: str, thread_id: str, received_at: datetime, outline) -> ProcessedEmail:
    return ProcessedEmail(
        email_id=email_id,
        thread_id=thread_id,
        sender="alex@example.com",
        subject="Quick sync this week?",
        received_at=received_at,
        read_status=ReadStatus.READ,
        is_no_reply=False,
        reply_outline=outline,
        reply_outline_status=ReplyOutlineStatus.SUGGESTED if outline else ReplyOutlineStatus.NONE,
    )


def test_older_outlined_message_is_stale_when_newer_message_exists_in_thread():
    older = make_processed("e1", "thread-1", _NOW.replace(hour=8), outline=["Ack"])
    newer = make_processed("e2", "thread-1", _NOW.replace(hour=10), outline=None)

    result = find_stale_outlines([older, newer])

    assert [e.email_id for e in result] == ["e1"]


def test_newest_message_in_thread_is_never_stale_even_with_an_outline():
    older = make_processed("e1", "thread-1", _NOW.replace(hour=8), outline=["Ack"])
    newer = make_processed("e2", "thread-1", _NOW.replace(hour=10), outline=["Ack again"])

    result = find_stale_outlines([older, newer])

    assert [e.email_id for e in result] == ["e1"]


def test_single_message_thread_is_never_stale():
    only = make_processed("e1", "thread-1", _NOW, outline=["Ack"])

    assert find_stale_outlines([only]) == []


def test_email_without_an_outline_is_never_flagged_stale():
    older = make_processed("e1", "thread-1", _NOW.replace(hour=8), outline=None)
    newer = make_processed("e2", "thread-1", _NOW.replace(hour=10), outline=None)

    assert find_stale_outlines([older, newer]) == []


def test_different_threads_do_not_affect_each_other():
    thread_a_old = make_processed("a1", "thread-a", _NOW.replace(hour=8), outline=["Ack"])
    thread_a_new = make_processed("a2", "thread-a", _NOW.replace(hour=10), outline=None)
    thread_b_only = make_processed("b1", "thread-b", _NOW.replace(hour=8), outline=["Ack"])

    result = find_stale_outlines([thread_a_old, thread_a_new, thread_b_only])

    assert [e.email_id for e in result] == ["a1"]
