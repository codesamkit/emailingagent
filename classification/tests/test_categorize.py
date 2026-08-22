"""Tests for classification/categorize.py — free-form topic categorization.

Runs with stdlib unittest only; no LLM backend or API key needed —
categorize_topic is always reached through a fake client.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime

from classification import categorize
from models.schema import RawEmail, ReadStatus


def make_email(
    email_id: str = "e1",
    sender: str = "someone@example.com",
    subject: str = "Hello",
    body: str = "Hi there, just checking in.",
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
        headers={},
    )


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


class TestCategorizeTopic(unittest.TestCase):
    def test_returns_the_topic_from_a_structured_response(self):
        fake_client = _FakeClient({"reason": "hiring correspondence", "topic": "job application"})
        result = categorize.categorize_topic(make_email(), client=fake_client)
        self.assertEqual(result, "job application")

    def test_prompt_uses_only_sender_subject_and_body_snippet(self):
        fake_client = _FakeClient({"reason": "r", "topic": "t"})
        long_body = "\n".join(f"line {i}" for i in range(20))
        email = make_email(sender="a@b.com", subject="Subj", body=long_body)

        categorize.categorize_topic(email, client=fake_client)

        sent_message = fake_client.messages.last_kwargs["messages"][0]["content"]
        self.assertIn("a@b.com", sent_message)
        self.assertIn("Subj", sent_message)
        self.assertIn("line 0", sent_message)
        self.assertNotIn("line 10", sent_message)

    def test_schema_declares_reason_before_topic(self):
        """Field order is load-bearing under constrained decoding — the
        reasoning must be emitted before the answer it informs."""
        fake_client = _FakeClient({"reason": "r", "topic": "t"})
        categorize.categorize_topic(make_email(), client=fake_client)

        schema = fake_client.messages.last_kwargs["output_config"]["format"]["schema"]
        self.assertEqual(list(schema["properties"]), ["reason", "topic"])
        self.assertFalse(schema["additionalProperties"])
        for prop in schema["properties"].values():
            self.assertIn("maxLength", prop)


class TestNormalizeTopic(unittest.TestCase):
    """The code-level cleanup applied to whatever the model returns."""

    def test_lowercases_and_trims(self):
        self.assertEqual(categorize._normalize_topic("  Job Application "), "job application")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(categorize._normalize_topic("order   \n shipping"), "order shipping")

    def test_caps_at_three_words(self):
        self.assertEqual(
            categorize._normalize_topic("a very long descriptive topic label"),
            "a very long",
        )

    def test_strips_stray_quotes_and_trailing_period(self):
        self.assertEqual(categorize._normalize_topic('"billing."'), "billing")

    def test_empty_response_falls_back_to_other(self):
        self.assertEqual(categorize._normalize_topic(""), "other")
        self.assertEqual(categorize._normalize_topic("   "), "other")

    def test_normalization_is_applied_to_the_llm_answer(self):
        fake_client = _FakeClient({"reason": "r", "topic": "  Team   PLANNING  "})
        result = categorize.categorize_topic(make_email(), client=fake_client)
        self.assertEqual(result, "team planning")


if __name__ == "__main__":
    unittest.main()
