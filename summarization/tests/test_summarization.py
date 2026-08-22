"""Tests for summarization/ — factual email summaries, single and batched.

Runs with stdlib unittest only; no `anthropic` package or API key needed —
summarize()/summarize_batch() are always reached through a fake client.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime

from models.schema import ReadStatus, RawEmail
from summarization import batch, summarize

LONG_BODY = (
    "Hi team,\n\n"
    "Following up on last week's planning session — we walked through the Q3 "
    "roadmap and there were a few open questions I want to close out before "
    "we finalize scope. First, the migration work for the billing service: "
    "engineering flagged that the current timeline assumes no downtime, but "
    "based on the schema changes we discussed, there's a real chance we'll "
    "need a short maintenance window. Can someone from infra confirm whether "
    "that's feasible, and if so, what the blast radius looks like for "
    "customers on the legacy plan?\n\n"
    "Second, marketing asked whether the new onboarding flow will be ready "
    "in time for the September launch event. I know design is still "
    "iterating on the empty-state screens, so I don't want to overpromise, "
    "but it would help to have a rough answer by end of week so they can "
    "plan the announcement copy.\n\n"
    "Finally, a reminder that budget requests for Q4 headcount are due "
    "Friday, August 28th. If your team needs additional hires, please get "
    "the request into the tracker before then — anything submitted after "
    "the deadline will roll to the next planning cycle.\n\n"
    "Let me know if you have questions.\n\nThanks,\nJordan"
)

SHORT_BODY = "Lunch Friday?"


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


class _FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, payload: dict):
        self.content = [_FakeBlock(json.dumps(payload))]


class _FakeMessages:
    """Returns queued payloads in order (one per create() call). A single
    payload (not a list) is returned for every call."""

    def __init__(self, payloads: dict | list[dict]):
        self._queue = list(payloads) if isinstance(payloads, list) else None
        self._fixed = payloads if not isinstance(payloads, list) else None
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._queue.pop(0) if self._queue is not None else self._fixed
        return _FakeResponse(payload)


class _FakeClient:
    def __init__(self, payloads: dict | list[dict]):
        self.messages = _FakeMessages(payloads)


class TestSummarize(unittest.TestCase):
    def test_parses_structured_response(self):
        fake_client = _FakeClient({
            "summary": "Jordan recaps Q3 planning and asks three questions.",
            "dates": ["Friday, August 28th"],
        })
        email = make_email(body=LONG_BODY)

        summary, dates = summarize.summarize(email, client=fake_client)

        self.assertEqual(summary, "Jordan recaps Q3 planning and asks three questions.")
        self.assertEqual(dates, ["Friday, August 28th"])

    def test_no_dates_mentioned_returns_empty_list_not_none(self):
        fake_client = _FakeClient({"summary": "Wants to grab lunch Friday.", "dates": []})
        email = make_email(sender="pat@example.com", subject="Lunch?", body=SHORT_BODY)

        _, dates = summarize.summarize(email, client=fake_client)

        self.assertEqual(dates, [])

    def test_prompt_includes_sender_subject_and_body(self):
        fake_client = _FakeClient({"summary": "Wants to grab lunch Friday.", "dates": []})
        email = make_email(sender="pat@example.com", subject="Lunch?", body=SHORT_BODY)

        summarize.summarize(email, client=fake_client)

        sent_message = fake_client.messages.calls[0]["messages"][0]["content"]
        self.assertIn("pat@example.com", sent_message)
        self.assertIn("Lunch?", sent_message)
        self.assertIn(SHORT_BODY, sent_message)


class TestSummarizeBatch(unittest.TestCase):
    """Fixture batch includes one long email and one very short email, per
    PHASES.md Phase 4's test requirement."""

    def test_empty_list_returns_empty_dict_without_calling_client(self):
        fake_client = _FakeClient({"summaries": []})

        result = batch.summarize_batch([], client=fake_client)

        self.assertEqual(result, {})
        self.assertEqual(fake_client.messages.calls, [])

    def test_batch_returns_dict_keyed_by_email_id(self):
        long_email = make_email(email_id="long1", sender="jordan@example.com", subject="Q3 planning follow-up", body=LONG_BODY)
        short_email = make_email(email_id="short1", sender="pat@example.com", subject="Lunch?", body=SHORT_BODY)

        fake_client = _FakeClient(
            {
                "summaries": [
                    {
                        "email_id": "long1",
                        "summary": (
                            "Jordan recaps Q3 planning and asks infra to confirm a maintenance "
                            "window for the billing migration, asks whether onboarding will be "
                            "ready for the September launch, and reminds the team that Q4 "
                            "headcount requests are due Friday, August 28th."
                        ),
                        "dates": ["Friday, August 28th"],
                    },
                    {"email_id": "short1", "summary": "Pat asks if the recipient is free for lunch Friday.", "dates": []},
                ]
            }
        )

        result = batch.summarize_batch([long_email, short_email], client=fake_client)

        self.assertEqual(set(result.keys()), {"long1", "short1"})
        self.assertIn("August 28th", result["long1"][0])
        self.assertIn("lunch", result["short1"][0].lower())
        self.assertEqual(result["long1"][1], ["Friday, August 28th"])
        # Single call covers both emails since batch_size (20) isn't exceeded.
        self.assertEqual(len(fake_client.messages.calls), 1)
        sent_message = fake_client.messages.calls[0]["messages"][0]["content"]
        self.assertIn("email_id: long1", sent_message)
        self.assertIn("email_id: short1", sent_message)

    def test_chunks_calls_when_batch_size_exceeded(self):
        emails = [make_email(email_id=f"e{i}") for i in range(5)]
        payloads = [
            {"summaries": [
                {"email_id": "e0", "summary": "s0", "dates": []},
                {"email_id": "e1", "summary": "s1", "dates": []},
            ]},
            {"summaries": [
                {"email_id": "e2", "summary": "s2", "dates": []},
                {"email_id": "e3", "summary": "s3", "dates": []},
            ]},
            {"summaries": [{"email_id": "e4", "summary": "s4", "dates": []}]},
        ]
        fake_client = _FakeClient(payloads)

        result = batch.summarize_batch(emails, client=fake_client, batch_size=2)

        self.assertEqual(len(fake_client.messages.calls), 3)
        self.assertEqual(
            result,
            {"e0": ("s0", []), "e1": ("s1", []), "e2": ("s2", []), "e3": ("s3", []), "e4": ("s4", [])},
        )

    def test_same_summary_instructions_as_single_summarize(self):
        # Ensures batch.py didn't fork its own copy of the factual/no-inference
        # rules — see summarize.SUMMARY_INSTRUCTIONS (DRY, per CLAUDE.md).
        self.assertIn(summarize.SUMMARY_INSTRUCTIONS, batch.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
