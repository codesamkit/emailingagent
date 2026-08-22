"""Tests for classification/ — no-reply detection.

Runs with stdlib unittest only; no `anthropic` package or API key needed —
classify_ambiguous is always reached through a mock/fake client.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from classification import classify, llm_fallback, rules
from models.schema import ReadStatus, RawEmail


def make_email(
    email_id: str = "e1",
    sender: str = "someone@example.com",
    subject: str = "Hello",
    body: str = "Hi there, just checking in.",
    headers: dict[str, str] | None = None,
) -> RawEmail:
    return RawEmail(
        email_id=email_id,
        thread_id="t1",
        sender=sender,
        recipients=["me@example.com"],
        subject=subject,
        body=body,
        received_at=datetime(2026, 8, 20, 9, 0, 0),
        read_status=ReadStatus.UNREAD,
        headers=headers or {},
    )


class TestClassifyIsNoReply(unittest.TestCase):
    """End-to-end classify.is_no_reply() cases, per PHASES.md Phase 2 spec."""

    @patch("classification.classify.classify_ambiguous")
    def test_obvious_no_reply_sender_pattern(self, mock_llm):
        email = make_email(sender="no-reply@service.com", subject="Your receipt")

        result = classify.is_no_reply(email)

        self.assertEqual(result, (True, "sender local-part matches no-reply pattern (no-reply@...)"))
        mock_llm.assert_not_called()

    @patch("classification.classify.classify_ambiguous")
    def test_obvious_personal(self, mock_llm):
        email = make_email(sender="jane.doe@gmail.com", subject="Lunch Friday?")

        result = classify.is_no_reply(email)

        self.assertEqual(result, (False, "personal sender, no bulk headers"))
        mock_llm.assert_not_called()

    @patch("classification.classify.classify_ambiguous")
    def test_ambiguous_newsletter_with_real_reply_to(self, mock_llm):
        mock_llm.return_value = (True, "bulk newsletter content despite real reply-to")
        email = make_email(
            sender="newsletter@company.com",
            subject="This week in tech",
            headers={
                "List-Unsubscribe": "<mailto:unsub@company.com>",
                "Reply-To": "editor@company.com",
            },
        )

        result = classify.is_no_reply(email)

        mock_llm.assert_called_once_with(email)
        self.assertEqual(result, mock_llm.return_value)

    @patch("classification.classify.classify_ambiguous")
    def test_ambiguous_shared_inbox_alert(self, mock_llm):
        mock_llm.return_value = (False, "monitored shared inbox, replies are read by a human")
        email = make_email(
            sender="alerts@team.company.com",
            subject="New ticket assigned to your team",
            body="Hi team, a new ticket came in — can someone take a look?",
        )

        result = classify.is_no_reply(email)

        mock_llm.assert_called_once_with(email)
        self.assertEqual(result, mock_llm.return_value)

    @patch("classification.classify.classify_ambiguous")
    def test_ambiguous_calendar_invite(self, mock_llm):
        mock_llm.return_value = (False, "calendar invite awaiting RSVP")
        email = make_email(
            sender="organizer@company.com",
            subject="Invitation: Project sync @ Thu 2pm",
            body="You have been invited to the following event.",
            headers={"Auto-Submitted": "auto-generated"},
        )

        result = classify.is_no_reply(email)

        mock_llm.assert_called_once_with(email)
        self.assertEqual(result, mock_llm.return_value)


class TestApplyRules(unittest.TestCase):
    """Direct unit tests of the rule-based tiering logic."""

    def test_precedence_bulk_alone_is_confident_positive(self):
        email = make_email(sender="team@company.com", headers={"Precedence": "bulk"})
        result = rules.apply_rules(email)
        self.assertEqual(result, (True, "Precedence header indicates bulk mail (Precedence: bulk)"))

    def test_list_unsubscribe_without_distinct_reply_to_is_confident_positive(self):
        email = make_email(
            sender="digest@company.com",
            headers={"List-Unsubscribe": "<mailto:unsub@company.com>"},
        )
        is_no_reply, _ = rules.apply_rules(email)
        self.assertTrue(is_no_reply)

    def test_weak_prefix_with_corroboration_is_confident_positive(self):
        email = make_email(
            sender="notifications@app.com",
            headers={"Precedence": "bulk"},
        )
        is_no_reply, _ = rules.apply_rules(email)
        self.assertTrue(is_no_reply)

    def test_weak_prefix_without_corroboration_is_ambiguous(self):
        email = make_email(sender="alerts@team.company.com")
        self.assertIsNone(rules.apply_rules(email))

    def test_list_unsubscribe_with_distinct_reply_to_is_ambiguous(self):
        email = make_email(
            sender="newsletter@company.com",
            headers={
                "List-Unsubscribe": "<mailto:unsub@company.com>",
                "Reply-To": "editor@company.com",
            },
        )
        self.assertIsNone(rules.apply_rules(email))

    def test_auto_submitted_alone_is_ambiguous(self):
        email = make_email(sender="organizer@company.com", headers={"Auto-Submitted": "auto-generated"})
        self.assertIsNone(rules.apply_rules(email))


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


class TestClassifyAmbiguous(unittest.TestCase):
    """Unit tests of llm_fallback.classify_ambiguous against a fake client
    (no real network/API key involved)."""

    def test_parses_structured_response(self):
        # The wire schema answers "deserves_reply"; classify_ambiguous
        # inverts it into the frozen (is_no_reply, reason) return shape.
        fake_client = _FakeClient({"reason": "bulk marketing content", "deserves_reply": False})
        email = make_email(sender="promo@shop.com", subject="50% off everything")

        result = llm_fallback.classify_ambiguous(email, client=fake_client)

        self.assertEqual(result, (True, "bulk marketing content"))

    def test_deserves_reply_maps_to_not_no_reply(self):
        fake_client = _FakeClient({"reason": "personal note", "deserves_reply": True})
        email = make_email(sender="friend@gmail.com", subject="Lunch?")

        result = llm_fallback.classify_ambiguous(email, client=fake_client)

        self.assertEqual(result, (False, "personal note"))

    def test_prompt_uses_only_sender_subject_and_body_snippet(self):
        fake_client = _FakeClient({"reason": "personal note", "deserves_reply": True})
        long_body = "\n".join(f"line {i}" for i in range(20))
        email = make_email(sender="a@b.com", subject="Subj", body=long_body)

        llm_fallback.classify_ambiguous(email, client=fake_client)

        sent_message = fake_client.messages.last_kwargs["messages"][0]["content"]
        self.assertIn("a@b.com", sent_message)
        self.assertIn("Subj", sent_message)
        self.assertIn("line 0", sent_message)
        self.assertNotIn("line 10", sent_message)


if __name__ == "__main__":
    unittest.main()
