"""Gate behavior: what trips it, what must not, and what shapes it accepts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pytest

from calendaring import samples
from calendaring.scheduling_intent import (
    is_scheduling_related,
    scheduling_signals,
)


class TestObviousPositives:
    @pytest.mark.parametrize(
        "email",
        samples.SCHEDULING_SAMPLES,
        ids=[e.email_id for e in samples.SCHEDULING_SAMPLES],
    )
    def test_sample_is_scheduling_related(self, email):
        assert is_scheduling_related(email) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Are you free Thursday at 1pm?",
            "Can we schedule a call next week?",
            "I need to reschedule our 1:1.",
            "What time works for you on Friday?",
            "Let me know your availability.",
            "Want to set up a time to chat?",
            "Could we find a time to sync on this?",
            "Does Tuesday work for you?",
            "I'll send a calendar invite once you confirm.",
            "Can we move our meeting to the afternoon?",
            "Happy to hop on a call whenever suits.",
            "Let's block off an hour next week.",
        ],
    )
    def test_common_phrasings(self, text):
        email = samples._email("t", "a@b.example", "Hello", text)
        assert is_scheduling_related(email) is True


class TestObviousNegatives:
    @pytest.mark.parametrize(
        "email",
        samples.NON_SCHEDULING_SAMPLES,
        ids=[e.email_id for e in samples.NON_SCHEDULING_SAMPLES],
    )
    def test_sample_is_not_scheduling_related(self, email):
        assert is_scheduling_related(email) is False

    @pytest.mark.parametrize(
        "text",
        [
            "Did the migration script get merged?",
            "Here is the report you asked for.",
            "Your password was changed successfully.",
            "Please review the attached contract.",
            "The build is failing on main.",
        ],
    )
    def test_non_scheduling_phrasings(self, text):
        email = samples._email("t", "a@b.example", "Hello", text)
        assert is_scheduling_related(email) is False

    def test_single_weak_signal_is_not_enough(self):
        """One ambiguous word must not trigger a Calendar API call."""
        email = samples._email("t", "a@b.example", "Notes", "Recap from the meeting.")
        assert is_scheduling_related(email) is False


class TestCalendarInviteMime:
    def test_text_calendar_content_type_is_decisive(self):
        email = samples._email(
            "t", "a@b.example", "Invitation", "See attached.",
            headers={"Content-Type": 'text/calendar; charset="UTF-8"; method=REQUEST'},
        )
        assert is_scheduling_related(email) is True

    def test_method_request_alone_is_decisive(self):
        email = samples._email(
            "t", "a@b.example", "Invitation", "See attached.",
            headers={"Content-Type": "multipart/mixed; method=REQUEST"},
        )
        assert is_scheduling_related(email) is True

    def test_header_lookup_is_case_insensitive(self):
        email = samples._email(
            "t", "a@b.example", "Invitation", "See attached.",
            headers={"content-type": "text/calendar"},
        )
        assert is_scheduling_related(email) is True

    def test_plain_content_type_is_not_an_invite(self):
        email = samples._email(
            "t", "a@b.example", "Hello", "Just a note.",
            headers={"Content-Type": "text/plain; charset=UTF-8"},
        )
        assert is_scheduling_related(email) is False


class TestBulkMailDiscount:
    def test_newsletter_with_weak_signals_is_discounted(self):
        """The false positive a naive keyword match produces."""
        signals = scheduling_signals(samples.NEWSLETTER_WEBINAR)
        assert signals.is_bulk is True
        assert signals.is_scheduling_related is False
        assert any("discounted" in reason for reason in signals.reasons)

    def test_bulk_headers_do_not_suppress_an_explicit_ask(self):
        """A real invite or explicit ask still counts, even from automation."""
        email = samples._email(
            "t", "noreply@vendor.example", "Your onboarding",
            "Are you free Tuesday? Let us know your availability.",
            headers={"List-Unsubscribe": "<https://v.example/u>", "Precedence": "bulk"},
        )
        assert is_scheduling_related(email) is True

    def test_bulk_headers_do_not_suppress_a_real_invite(self):
        email = samples._email(
            "t", "calendar-notification@google.com", "Invitation: Sync",
            "You have been invited.",
            headers={"Content-Type": "text/calendar; method=REQUEST",
                     "Auto-Submitted": "auto-generated"},
        )
        assert is_scheduling_related(email) is True


class TestDuckTyping:
    """Works with both RawEmail shapes currently in the repo.

    `models/schema.py` names the field `body`; `ingestion/models.py` names it
    `body_text`. Until those are reconciled, the gate must accept both — Track
    C builds against the schema, Track A produces the ingestion shape.
    """

    @dataclass
    class IngestionShaped:
        email_id: str
        subject: str
        body_text: str
        snippet: str = ""
        headers: Dict[str, str] = None

        def __post_init__(self):
            self.headers = self.headers or {}

    def test_accepts_ingestion_body_text(self):
        email = self.IngestionShaped(
            email_id="x", subject="Sync", body_text="Are you free Thursday?"
        )
        assert is_scheduling_related(email) is True

    def test_falls_back_to_snippet(self):
        email = self.IngestionShaped(
            email_id="x", subject="Sync", body_text="", snippet="Can we schedule a call?"
        )
        assert is_scheduling_related(email) is True

    def test_missing_fields_do_not_crash(self):
        class Bare:
            pass

        assert is_scheduling_related(Bare()) is False


class TestNoSideEffects:
    def test_gate_makes_no_api_or_llm_call(self, monkeypatch):
        """The whole point of the gate is that it is free to run."""
        import calendaring.context as context_module

        def explode(*args, **kwargs):
            raise AssertionError("gate must not touch the Calendar API")

        monkeypatch.setattr(context_module, "get_calendar_context", explode)
        for email in samples.ALL_SAMPLES:
            is_scheduling_related(email)

    def test_long_body_is_truncated(self):
        """Quoted thread history below the fold must not drive the verdict."""
        padding = "x" * 5000
        email = samples._email(
            "t", "a@b.example", "Re: notes", padding + " Are you free Thursday?"
        )
        assert is_scheduling_related(email) is False


class TestSignalsDetail:
    def test_reasons_are_populated_for_debugging(self):
        signals = scheduling_signals(samples.DIRECT_ASK)
        assert signals.score >= 2
        assert signals.reasons

    def test_score_zero_for_unrelated(self):
        assert scheduling_signals(samples.PROJECT_QUESTION).score == 0
