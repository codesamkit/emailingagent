"""Tests for summarization/action_items.py — extracted action items.

Runs with stdlib unittest only; no `anthropic` package or API key needed —
extract_action_items() is always reached through a fake client.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from models.schema import ReadStatus, RawEmail
from summarization import action_items
from summarization.tests.test_summarization import _FakeClient


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
    )


class TestExtractActionItems(unittest.TestCase):
    def test_parses_structured_response(self):
        fake_client = _FakeClient({
            "action_items": ["Send the signed contract by Friday", "Confirm the headcount"],
        })
        email = make_email(body="Please send the signed contract by Friday and confirm headcount.")

        items = action_items.extract_action_items(email, client=fake_client)

        self.assertEqual(
            items, ["Send the signed contract by Friday", "Confirm the headcount"]
        )

    def test_no_action_needed_returns_empty_list_not_none(self):
        fake_client = _FakeClient({"action_items": []})
        email = make_email(subject="FYI", body="Just looping you in, no action needed.")

        items = action_items.extract_action_items(email, client=fake_client)

        self.assertEqual(items, [])

    def test_runs_regardless_of_read_status_or_sender_type(self):
        """Unlike reply_outline, this stage has no code-level gate — a
        no-reply notification (e.g. a bill) can still carry a real
        deadline, so extraction runs for every email."""
        fake_client = _FakeClient({"action_items": ["Pay the invoice by Sept 1"]})
        email = make_email(sender="billing@service.example", body="Invoice due Sept 1.")

        items = action_items.extract_action_items(email, client=fake_client)

        self.assertEqual(items, ["Pay the invoice by Sept 1"])


if __name__ == "__main__":
    unittest.main()
