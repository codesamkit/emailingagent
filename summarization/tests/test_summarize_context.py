"""PHASES-COMPLEX.md B5: summarize() accepts an optional ContextPack.
Kept separate from test_summarization.py, which must pass unchanged."""

from __future__ import annotations

import json
import unittest
from datetime import datetime

from models.schema import ContextPack, ContextSection, ReadStatus, RawEmail
from summarization import summarize


def make_email(body: str = "Hi there, just checking in.") -> RawEmail:
    return RawEmail(
        email_id="e1",
        thread_id="t1",
        sender="someone@example.com",
        recipients=["me@example.com"],
        subject="Hello",
        body=body,
        received_at=datetime(2026, 8, 20, 9, 0, 0),
        read_status=ReadStatus.UNREAD,
    )


def make_pack() -> ContextPack:
    return ContextPack(
        anchor_email_id="e1",
        sections=[
            ContextSection(
                label="Case: Q3 renewal",
                text="The renewal was approved last week at the discounted rate.",
                source_email_ids=["e0"],
                score=0.9,
            )
        ],
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
        self.calls: list = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._payload)


class _FakeClient:
    def __init__(self, payload: dict):
        self.messages = _FakeMessages(payload)


class TestSummarizeContext(unittest.TestCase):
    def test_omitting_context_preserves_prior_prompt_exactly(self):
        client = _FakeClient({"bullets": ["A check-in.", "Nothing is asked of you.", "No deadline stated."], "dates": []})
        summarize.summarize(make_email(), client=client)
        prompt = client.messages.calls[0]["messages"][0]["content"]
        self.assertNotIn("What you already know:", prompt)

    def test_context_is_folded_into_the_prompt(self):
        client = _FakeClient({"bullets": ["A check-in.", "Nothing is asked of you.", "No deadline stated."], "dates": []})
        summarize.summarize(make_email(), client=client, context=make_pack())
        prompt = client.messages.calls[0]["messages"][0]["content"]
        self.assertIn("What you already know:", prompt)
        self.assertIn("renewal was approved", prompt)

    def test_empty_context_pack_adds_nothing(self):
        client = _FakeClient({"bullets": ["A check-in.", "Nothing is asked of you.", "No deadline stated."], "dates": []})
        summarize.summarize(make_email(), client=client, context=ContextPack())
        prompt = client.messages.calls[0]["messages"][0]["content"]
        self.assertNotIn("What you already know:", prompt)


if __name__ == "__main__":
    unittest.main()
