"""PHASES-COMPLEX.md B5: outline generation accepts an optional ContextPack
and folds it into the prompt as a "What you already know:" section. Kept
separate from test_outline_gating.py, which must pass completely unchanged."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from drafting.outline import generate_reply_outline
from models.schema import ContextPack, ContextSection, ProcessedEmail, RawEmail, ReadStatus

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    def __init__(self, bullets: Optional[List[str]] = None):
        self.bullets = bullets or ["Acknowledge the request", "Confirm the deadline"]
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(json.dumps({"bullets": self.bullets}))


def processed(**overrides) -> ProcessedEmail:
    defaults = dict(
        email_id="e1",
        thread_id="t1",
        sender="Dana Reed <dana@example.com>",
        subject="Q3 numbers",
        received_at=NOW,
        read_status=ReadStatus.READ,
        is_no_reply=False,
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


def raw(**overrides) -> RawEmail:
    defaults = dict(
        email_id="e1",
        thread_id="t1",
        sender="Dana Reed <dana@example.com>",
        recipients=["me@example.com"],
        subject="Q3 numbers",
        body="Can you confirm the Q3 numbers before Friday?",
        received_at=NOW,
        read_status=ReadStatus.READ,
    )
    defaults.update(overrides)
    return RawEmail(**defaults)


def _pack() -> ContextPack:
    return ContextPack(
        anchor_email_id="e1",
        sections=[
            ContextSection(
                label="Case: Q3 renewal",
                text="The Q3 renewal was already approved at the discounted rate.",
                source_email_ids=["e0"],
                score=0.9,
            )
        ],
    )


def test_omitting_context_preserves_prior_prompt_exactly():
    client = FakeClient()
    generate_reply_outline(processed(), raw(), client=client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "What you already know:" not in prompt


def test_context_is_folded_into_the_prompt():
    client = FakeClient()
    generate_reply_outline(processed(), raw(), client=client, context=_pack())
    prompt = client.calls[0]["messages"][0]["content"]
    assert "What you already know:" in prompt
    assert "Q3 renewal was already approved" in prompt


def test_empty_context_pack_adds_nothing():
    client = FakeClient()
    generate_reply_outline(processed(), raw(), client=client, context=ContextPack())
    prompt = client.calls[0]["messages"][0]["content"]
    assert "What you already know:" not in prompt


def test_ineligible_email_still_makes_no_call_with_context_present():
    """Drafting eligibility no longer depends on read status — a no-reply
    sender is the ineligible case here, not an unread one."""
    from drafting.tests.test_outline_gating import ExplodingClient

    result, status = generate_reply_outline(
        processed(is_no_reply=True),
        raw(),
        client=ExplodingClient(),
        context=_pack(),
    )
    assert result is None
