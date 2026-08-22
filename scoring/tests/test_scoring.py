"""Tests for scoring/ — importance ranking.

Runs with stdlib unittest only; no `anthropic` package or API key needed —
score_importance is always reached through a fake client.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus, RawEmail
from scoring import filters, score
from scoring.signals import compute_signals


def make_email(
    email_id: str = "e1",
    sender: str = "someone@example.com",
    subject: str = "Hello",
    body: str = "Hi there, just checking in.",
    received_at: datetime | None = None,
    read_status: ReadStatus = ReadStatus.UNREAD,
    headers: dict[str, str] | None = None,
) -> RawEmail:
    return RawEmail(
        email_id=email_id,
        thread_id="t1",
        sender=sender,
        recipients=["iamsamkitshah@gmail.com"],
        subject=subject,
        body=body,
        received_at=received_at or datetime(2026, 8, 20, 9, 0, 0),
        read_status=read_status,
        headers=headers or {},
    )


class TestComputeSignals(unittest.TestCase):
    def test_vip_sender_flagged_when_in_injected_set(self):
        email = make_email(sender="boss@company.com")
        signals = compute_signals(email, is_no_reply=False, vip_senders=frozenset({"boss@company.com"}))
        self.assertTrue(signals["is_vip"])

    def test_non_vip_sender_not_flagged(self):
        email = make_email(sender="stranger@company.com")
        signals = compute_signals(email, is_no_reply=False, vip_senders=frozenset({"boss@company.com"}))
        self.assertFalse(signals["is_vip"])

    def test_direct_when_owner_in_to(self):
        email = make_email(headers={"To": "iamsamkitshah@gmail.com"})
        signals = compute_signals(email, is_no_reply=False)
        self.assertTrue(signals["is_direct"])

    def test_direct_when_to_header_missing(self):
        email = make_email(headers={})
        signals = compute_signals(email, is_no_reply=False)
        self.assertTrue(signals["is_direct"])

    def test_not_direct_when_owner_only_in_cc(self):
        email = make_email(
            headers={"To": "someone.else@company.com", "Cc": "iamsamkitshah@gmail.com"}
        )
        signals = compute_signals(email, is_no_reply=False)
        self.assertFalse(signals["is_direct"])

    def test_urgency_keywords_found(self):
        email = make_email(subject="URGENT: action required")
        signals = compute_signals(email, is_no_reply=False)
        self.assertIn("urgent", signals["urgency_keyword_hits"])
        self.assertIn("action required", signals["urgency_keyword_hits"])

    def test_urgency_keywords_not_found(self):
        email = make_email(subject="Lunch Friday?", body="Want to grab lunch?")
        signals = compute_signals(email, is_no_reply=False)
        self.assertEqual(signals["urgency_keyword_hits"], [])

    def test_unread_age_present_only_when_unread(self):
        now = datetime(2026, 8, 22, 9, 0, 0)
        received = now - timedelta(hours=48)

        unread_email = make_email(received_at=received, read_status=ReadStatus.UNREAD)
        unread_signals = compute_signals(unread_email, is_no_reply=False, now=now)
        self.assertEqual(unread_signals["unread_age_hours"], 48.0)

        read_email = make_email(received_at=received, read_status=ReadStatus.READ)
        read_signals = compute_signals(read_email, is_no_reply=False, now=now)
        self.assertIsNone(read_signals["unread_age_hours"])

    def test_is_no_reply_passed_through(self):
        email = make_email()
        self.assertTrue(compute_signals(email, is_no_reply=True)["is_no_reply"])
        self.assertFalse(compute_signals(email, is_no_reply=False)["is_no_reply"])


class TestLevelFromScore(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            (0.0, ImportanceLevel.LOW),
            (24.9, ImportanceLevel.LOW),
            (25.0, ImportanceLevel.MEDIUM),
            (49.9, ImportanceLevel.MEDIUM),
            (50.0, ImportanceLevel.HIGH),
            (74.9, ImportanceLevel.HIGH),
            (75.0, ImportanceLevel.URGENT),
            (100.0, ImportanceLevel.URGENT),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(score._level_from_score(value), expected)


class _FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, payload: dict):
        self.content = [_FakeBlock(json.dumps(payload))]


class _FakeMessages:
    def __init__(self, payload: dict):
        self._payload = payload
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(self._payload)


class _FakeClient:
    def __init__(self, payload: dict):
        self.messages = _FakeMessages(payload)


class TestScoreImportance(unittest.TestCase):
    def test_parses_structured_response(self):
        fake_client = _FakeClient({"importance_score": 62, "justification": "direct ask, moderate urgency"})
        email = make_email(subject="Can you review this by Friday?")

        result = score.score_importance(email, is_no_reply=False, client=fake_client)

        self.assertEqual(result, (62.0, ImportanceLevel.HIGH, "direct ask, moderate urgency"))

    def test_score_clamped_above_range(self):
        fake_client = _FakeClient({"importance_score": 150, "justification": "whatever"})
        email = make_email()

        value, level, _ = score.score_importance(email, is_no_reply=False, client=fake_client)

        self.assertEqual(value, 100.0)
        self.assertEqual(level, ImportanceLevel.URGENT)

    def test_score_clamped_below_range(self):
        fake_client = _FakeClient({"importance_score": -20, "justification": "whatever"})
        email = make_email()

        value, level, _ = score.score_importance(email, is_no_reply=False, client=fake_client)

        self.assertEqual(value, 0.0)
        self.assertEqual(level, ImportanceLevel.LOW)

    def test_signals_included_in_prompt(self):
        fake_client = _FakeClient({"importance_score": 10, "justification": "no-reply"})
        email = make_email(sender="no-reply@service.com", subject="Your receipt")

        score.score_importance(email, is_no_reply=True, client=fake_client)

        sent_message = fake_client.messages.last_kwargs["messages"][0]["content"]
        self.assertIn("Classified as no-reply/automated: True", sent_message)


def make_processed(
    email_id: str,
    importance_score: float | None,
    importance_level: ImportanceLevel | None,
    read_status: ReadStatus = ReadStatus.UNREAD,
) -> ProcessedEmail:
    return ProcessedEmail(
        email_id=email_id,
        thread_id="t1",
        sender="someone@example.com",
        subject="Subject",
        received_at=datetime(2026, 8, 20, 9, 0, 0),
        read_status=read_status,
        importance_score=importance_score,
        importance_level=importance_level,
    )


class TestFilters(unittest.TestCase):
    def test_top_n_orders_by_score_descending(self):
        emails = [
            make_processed("low", 10.0, ImportanceLevel.LOW),
            make_processed("high", 90.0, ImportanceLevel.URGENT),
            make_processed("mid", 50.0, ImportanceLevel.HIGH),
        ]
        result = filters.top_n(emails, 2)
        self.assertEqual([e.email_id for e in result], ["high", "mid"])

    def test_top_n_sorts_none_score_last(self):
        emails = [
            make_processed("unscored", None, None),
            make_processed("scored", 5.0, ImportanceLevel.LOW),
        ]
        result = filters.top_n(emails, 2)
        self.assertEqual([e.email_id for e in result], ["scored", "unscored"])

    def test_urgent_and_unread(self):
        emails = [
            make_processed("urgent_unread", 80.0, ImportanceLevel.URGENT, ReadStatus.UNREAD),
            make_processed("urgent_read", 80.0, ImportanceLevel.URGENT, ReadStatus.READ),
            make_processed("high_unread", 60.0, ImportanceLevel.HIGH, ReadStatus.UNREAD),
        ]
        result = filters.urgent_and_unread(emails)
        self.assertEqual([e.email_id for e in result], ["urgent_unread"])


if __name__ == "__main__":
    unittest.main()
