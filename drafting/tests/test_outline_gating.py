"""The Phase 5 correctness requirement: outlines are gated in code, not prompt.

interfaces/README.md is explicit that these tests must assert the LLM client
is never *invoked* for ineligible emails — not merely that the output is None.
A prompt-level gate would pass the weaker assertion and still cost tokens,
still leak the email to the model, and still be one clever email away from
drafting a reply to an unread no-reply notification.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from drafting.outline import generate_reply_outline, is_eligible
from models.schema import (
    CalendarContext,
    CalendarSlot,
    ProcessedEmail,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class ExplodingClient:
    """Any use of this fails the test — proves no LLM call was made."""

    def __getattr__(self, name):
        raise AssertionError(
            "LLM client was touched (.{0}) for an ineligible email".format(name)
        )


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    """Records calls and returns a canned outline."""

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


class TestUnreadIsNeverDrafted:
    def test_returns_none_and_status_none(self):
        result, status = generate_reply_outline(
            processed(read_status=ReadStatus.UNREAD), raw(), client=ExplodingClient()
        )
        assert result is None
        assert status == ReplyOutlineStatus.NONE

    def test_no_llm_call_is_made(self):
        """Not just a null result — the client must never be touched."""
        client = FakeClient()
        generate_reply_outline(
            processed(read_status=ReadStatus.UNREAD), raw(), client=client
        )
        assert client.calls == []

    def test_unread_wins_even_when_scheduling_related(self):
        result, status = generate_reply_outline(
            processed(read_status=ReadStatus.UNREAD, is_scheduling_related=True),
            raw(),
            client=ExplodingClient(),
        )
        assert (result, status) == (None, ReplyOutlineStatus.NONE)


class TestNoReplyIsNeverDrafted:
    def test_returns_none_and_not_applicable(self):
        result, status = generate_reply_outline(
            processed(is_no_reply=True), raw(), client=ExplodingClient()
        )
        assert result is None
        assert status == ReplyOutlineStatus.NOT_APPLICABLE

    def test_no_llm_call_is_made(self):
        client = FakeClient()
        generate_reply_outline(processed(is_no_reply=True), raw(), client=client)
        assert client.calls == []

    def test_no_reply_beats_read_status(self):
        """A read no-reply email is still never drafted."""
        _, status = generate_reply_outline(
            processed(read_status=ReadStatus.READ, is_no_reply=True),
            raw(),
            client=ExplodingClient(),
        )
        assert status == ReplyOutlineStatus.NOT_APPLICABLE

    def test_no_reply_beats_scheduling(self):
        _, status = generate_reply_outline(
            processed(is_no_reply=True, is_scheduling_related=True),
            raw(),
            client=ExplodingClient(),
        )
        assert status == ReplyOutlineStatus.NOT_APPLICABLE


class TestUnclassifiedIsNotDrafted:
    def test_is_no_reply_none_blocks_generation(self):
        """Classification has to run first — otherwise a no-reply email would
        be drafted during the window where is_no_reply is still None."""
        result, status = generate_reply_outline(
            processed(is_no_reply=None), raw(), client=ExplodingClient()
        )
        assert result is None
        assert status == ReplyOutlineStatus.NONE


class TestEligibleEmail:
    def test_read_and_not_no_reply_generates_bullets(self):
        client = FakeClient(["Confirm the Q3 figures", "Flag the Friday deadline"])
        bullets, status = generate_reply_outline(processed(), raw(), client=client)
        assert bullets == ["Confirm the Q3 figures", "Flag the Friday deadline"]
        assert status == ReplyOutlineStatus.SUGGESTED
        assert len(client.calls) == 1

    def test_prompt_includes_the_email_body(self):
        client = FakeClient()
        generate_reply_outline(processed(), raw(), client=client)
        sent = client.calls[0]["messages"][0]["content"]
        assert "Q3 numbers" in sent

    def test_summary_is_passed_when_available(self):
        client = FakeClient()
        generate_reply_outline(
            processed(summary="Dana wants Q3 numbers by Friday."), raw(), client=client
        )
        assert "Dana wants Q3 numbers by Friday." in client.calls[0]["messages"][0]["content"]

    def test_blank_bullets_are_dropped(self):
        client = FakeClient(["Real bullet", "   ", ""])
        bullets, _ = generate_reply_outline(processed(), raw(), client=client)
        assert bullets == ["Real bullet"]

    def test_long_body_is_truncated_before_sending(self):
        client = FakeClient()
        generate_reply_outline(processed(), raw(body="x" * 50_000), client=client)
        assert len(client.calls[0]["messages"][0]["content"]) < 10_000


class TestSchedulingEmailsGetRealTimes:
    def context_with_slots(self):
        return CalendarContext(
            range_start=NOW,
            range_end=NOW + timedelta(days=7),
            suggested_slots=[
                CalendarSlot(NOW + timedelta(days=1), NOW + timedelta(days=1, minutes=30)),
                CalendarSlot(NOW + timedelta(days=2), NOW + timedelta(days=2, minutes=30)),
            ],
        )

    def test_availability_bullet_is_appended(self):
        client = FakeClient(["Acknowledge the meeting request"])
        bullets, _ = generate_reply_outline(
            processed(is_scheduling_related=True, calendar_context=self.context_with_slots()),
            raw(),
            client=client,
        )
        assert len(bullets) == 2
        assert "Offer these times" in bullets[-1]
        assert "25 Aug" in bullets[-1]

    def test_model_is_told_not_to_guess_availability(self):
        client = FakeClient()
        generate_reply_outline(
            processed(is_scheduling_related=True, calendar_context=self.context_with_slots()),
            raw(),
            client=client,
        )
        assert "Do not propose specific times" in client.calls[0]["messages"][0]["content"]

    def test_fully_booked_calendar_produces_an_explicit_bullet(self):
        """Silence would read as 'no scheduling needed'; say so instead."""
        client = FakeClient(["Acknowledge the request"])
        booked = CalendarContext(
            range_start=NOW,
            range_end=NOW + timedelta(days=7),
            busy_blocks=[CalendarSlot(NOW, NOW + timedelta(days=7))],
        )
        bullets, _ = generate_reply_outline(
            processed(is_scheduling_related=True, calendar_context=booked),
            raw(),
            client=client,
        )
        assert "no open slots found" in bullets[-1]

    def test_empty_calendar_offers_times_rather_than_claiming_none(self):
        """A calendar with nothing on it means maximum availability, not none."""
        client = FakeClient(["Acknowledge the request"])
        empty = CalendarContext(range_start=NOW, range_end=NOW + timedelta(days=7))
        bullets, _ = generate_reply_outline(
            processed(is_scheduling_related=True, calendar_context=empty),
            raw(),
            client=client,
        )
        assert "Offer these times" in bullets[-1]

    def test_non_scheduling_email_gets_no_availability_bullet(self):
        client = FakeClient(["Confirm the figures"])
        bullets, _ = generate_reply_outline(
            processed(is_scheduling_related=False), raw(), client=client
        )
        assert bullets == ["Confirm the figures"]

    def test_unreadable_calendar_is_not_reported_as_no_availability(self):
        """"Could not check" and "nothing is free" are different facts, and
        only one of them is safe to imply in a reply the user might send."""
        client = FakeClient(["Acknowledge the request"])
        bullets, _ = generate_reply_outline(
            processed(is_scheduling_related=True, calendar_context=None),
            raw(),
            client=client,
        )
        assert "could not be checked" in bullets[-1]
        assert "no open slots" not in bullets[-1]


class TestIsEligible:
    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            (dict(), True),
            (dict(read_status=ReadStatus.UNREAD), False),
            (dict(is_no_reply=True), False),
            (dict(is_no_reply=None), False),
            (dict(is_no_reply=True, read_status=ReadStatus.UNREAD), False),
        ],
    )
    def test_eligibility_matrix(self, kwargs, expected):
        assert is_eligible(processed(**kwargs))[0] is expected

    def test_is_pure(self):
        """Safe for the pipeline to call repeatedly during incremental re-run."""
        record = processed()
        before = (record.read_status, record.is_no_reply, record.reply_outline_status)
        is_eligible(record)
        assert (record.read_status, record.is_no_reply, record.reply_outline_status) == before


class TestExpandStub:
    def test_raises_rather_than_returning_placeholder_text(self):
        from drafting.expand import NotYetImplementedError, expand_outline_to_full_draft

        with pytest.raises(NotYetImplementedError) as exc:
            expand_outline_to_full_draft("e1", ["a bullet"])
        assert "not implemented yet" in str(exc.value)
