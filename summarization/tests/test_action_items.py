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


class TestIsMeaningful(unittest.TestCase):
    """The response schema bounds length and count but cannot require
    content, and the model does emit degenerate output."""

    def test_rejects_punctuation_only(self):
        for junk in [",", "...", "   ", "1,2,3", "a,b"]:
            self.assertFalse(action_items.is_meaningful(junk), junk)

    def test_rejects_a_leaked_json_fragment(self):
        """Seen ten times over as a bare "," plus one "]}  deficiency:" in a
        real mailbox; both reached the to-do list as unreadable rows."""
        self.assertFalse(action_items.is_meaningful("]}  deficiency:"))

    def test_keeps_real_items_including_terse_ones(self):
        for item in [
            "Send the signed contract by Friday.",
            "Get Kenny Zhou on a call this week.",
            "Pay $500",
            "Task A",
        ]:
            self.assertTrue(action_items.is_meaningful(item), item)

    def test_extraction_drops_junk_and_strips(self):
        client = _FakeClient({"action_items": [",", "  Send the contract  "]})
        items = action_items.extract_action_items(
            RawEmail(
                email_id="e1", thread_id="t1", sender="a@b.com", recipients=["me@x.com"],
                subject="Hi", body="Body", received_at=datetime(2026, 8, 24),
                read_status=ReadStatus.READ,
            ),
            client=client,
        )
        self.assertEqual(items, ["Send the contract"])


if __name__ == "__main__":
    unittest.main()
