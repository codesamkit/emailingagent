from __future__ import annotations

from interface.filters import by_importance, by_no_reply, by_read_status, by_scheduling_related, sorted_by_importance
from interface.fixtures import demo_processed_emails
from models.schema import ImportanceLevel, ReadStatus


def test_by_read_status_filters_unread_only():
    emails = demo_processed_emails()
    result = by_read_status(emails, ReadStatus.UNREAD)
    assert {e.email_id for e in result} == {"demo-unread"}


def test_by_importance_filters_exact_level():
    emails = demo_processed_emails()
    result = by_importance(emails, ImportanceLevel.HIGH)
    assert {e.email_id for e in result} == {"demo-eligible", "demo-scheduling", "demo-stale"}


def test_by_no_reply_filters_flagged_only():
    emails = demo_processed_emails()
    result = by_no_reply(emails)
    assert {e.email_id for e in result} == {"demo-no-reply"}


def test_by_scheduling_related_filters_flagged_only():
    emails = demo_processed_emails()
    result = by_scheduling_related(emails)
    assert {e.email_id for e in result} == {"demo-scheduling"}


def test_sorted_by_importance_descending():
    emails = demo_processed_emails()
    result = sorted_by_importance(emails)
    scores = [e.importance_score for e in result]
    assert scores == sorted(scores, reverse=True)
