"""The same code-level-gate discipline drafting/outline.py uses, applied to
proposed-event extraction: is_scheduling_related must be checked with a
plain `if` before any LLM client is touched, and tests assert the client is
never invoked for a non-scheduling email — not merely that the result is
None. See interfaces/README.md's gating note.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from calendaring.propose import extract_proposed_event
from models.schema import ProcessedEmail, ProposedEventStatus, RawEmail, ReadStatus

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)  # a Monday


class ExplodingClient:
    """Any use of this fails the test — proves no LLM call was made."""

    def __getattr__(self, name):
        raise AssertionError(
            "LLM client was touched (.{0}) for a non-scheduling email".format(name)
        )


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    """Records calls and returns a canned extraction result."""

    def __init__(self, payload: Optional[dict] = None):
        self.payload = payload if payload is not None else {
            "found": True,
            "title": "Sync with Dana",
            "start": "2026-08-27T14:00:00+00:00",
            "end": "2026-08-27T14:30:00+00:00",
            "attendees": [],
            "location": "",
        }
        self.calls: List[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(json.dumps(self.payload))


def processed(**overrides) -> ProcessedEmail:
    defaults = dict(
        email_id="e1", thread_id="t1", sender="Dana Reed <dana@example.com>",
        subject="Sync?", received_at=NOW, read_status=ReadStatus.READ,
        is_scheduling_related=True,
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


def raw(**overrides) -> RawEmail:
    defaults = dict(
        email_id="e1", thread_id="t1", sender="Dana Reed <dana@example.com>",
        recipients=["me@example.com"], subject="Sync?",
        body="Are you free Thursday at 2pm to sync?",
        received_at=NOW, read_status=ReadStatus.READ,
    )
    defaults.update(overrides)
    return RawEmail(**defaults)


class TestNonSchedulingIsNeverExtracted:
    def test_returns_none_and_status_none(self):
        result, status = extract_proposed_event(
            processed(is_scheduling_related=False), raw(), client=ExplodingClient()
        )
        assert result is None
        assert status == ProposedEventStatus.NONE

    def test_no_llm_call_is_made(self):
        client = FakeClient()
        extract_proposed_event(processed(is_scheduling_related=False), raw(), client=client)
        assert client.calls == []

    def test_unset_scheduling_flag_is_also_treated_as_not_scheduling(self):
        result, status = extract_proposed_event(
            processed(is_scheduling_related=None), raw(), client=ExplodingClient()
        )
        assert (result, status) == (None, ProposedEventStatus.NONE)


class TestConcreteMeetingIsExtracted:
    def test_extracts_title_start_end(self):
        client = FakeClient()
        event, status = extract_proposed_event(processed(), raw(), client=client)
        assert status == ProposedEventStatus.SUGGESTED
        assert event.title == "Sync with Dana"
        assert event.start == datetime(2026, 8, 27, 14, tzinfo=UTC)
        assert event.end == datetime(2026, 8, 27, 14, 30, tzinfo=UTC)
        assert len(client.calls) == 1

    def test_sender_is_added_as_an_attendee_as_a_bare_address(self):
        """The sender arrives header-decorated ("Name <addr>"), but Google's
        attendee list takes bare addresses and rejects the whole insert if any
        entry isn't one."""
        client = FakeClient()
        event, _ = extract_proposed_event(processed(), raw(), client=client)
        assert event.attendees == ["dana@example.com"]

    def test_attendees_that_are_not_addresses_are_dropped(self):
        """The model routinely answers with display names. One of those used to
        fail the entire event with "Invalid attendee email."."""
        client = FakeClient({
            "found": True, "title": "Sync", "attendees": ["Ronith", "priya@example.com"],
            "start": "2026-08-27T14:00:00+00:00", "end": "2026-08-27T14:30:00+00:00",
            "location": "",
        })
        event, _ = extract_proposed_event(processed(), raw(), client=client)
        assert "Ronith" not in event.attendees
        assert event.attendees == ["priya@example.com", "dana@example.com"]

    def test_duplicate_addresses_are_collapsed(self):
        client = FakeClient({
            "found": True, "title": "Sync",
            "attendees": ["dana@example.com", "Dana Reed <dana@example.com>"],
            "start": "2026-08-27T14:00:00+00:00", "end": "2026-08-27T14:30:00+00:00",
            "location": "",
        })
        event, _ = extract_proposed_event(processed(), raw(), client=client)
        assert event.attendees == ["dana@example.com"]

    def test_explicit_attendees_are_kept(self):
        client = FakeClient({
            "found": True, "title": "Sync", "attendees": ["priya@example.com"],
            "start": "2026-08-27T14:00:00+00:00", "end": "2026-08-27T14:30:00+00:00",
            "location": "",
        })
        event, _ = extract_proposed_event(processed(), raw(), client=client)
        assert "priya@example.com" in event.attendees
        assert "dana@example.com" in event.attendees

    def test_location_is_passed_through(self):
        client = FakeClient({
            "found": True, "title": "Sync", "attendees": [],
            "start": "2026-08-27T14:00:00+00:00", "end": "2026-08-27T14:30:00+00:00",
            "location": "Room 2",
        })
        event, _ = extract_proposed_event(processed(), raw(), client=client)
        assert event.location == "Room 2"

    def test_missing_title_falls_back_to_sender(self):
        client = FakeClient({
            "found": True, "title": "", "attendees": [],
            "start": "2026-08-27T14:00:00+00:00", "end": "2026-08-27T14:30:00+00:00",
            "location": "",
        })
        event, _ = extract_proposed_event(processed(), raw(), client=client)
        assert "Dana Reed <dana@example.com>" in event.title

    def test_prompt_includes_received_at_for_relative_date_resolution(self):
        client = FakeClient()
        extract_proposed_event(processed(), raw(), client=client)
        sent = client.calls[0]["messages"][0]["content"]
        assert NOW.isoformat() in sent


class TestVagueSchedulingEmailsAreNotForced:
    def test_no_concrete_time_returns_none(self):
        client = FakeClient({
            "found": False, "title": "", "attendees": [], "start": "", "end": "", "location": "",
        })
        event, status = extract_proposed_event(processed(), raw(), client=client)
        assert event is None
        assert status == ProposedEventStatus.NONE

    def test_unparseable_time_is_discarded_not_raised(self):
        client = FakeClient({
            "found": True, "title": "Sync", "attendees": [],
            "start": "not-a-timestamp", "end": "also-not-one", "location": "",
        })
        event, status = extract_proposed_event(processed(), raw(), client=client)
        assert event is None
        assert status == ProposedEventStatus.NONE

    def test_end_before_start_is_discarded(self):
        client = FakeClient({
            "found": True, "title": "Sync", "attendees": [],
            "start": "2026-08-27T14:00:00+00:00", "end": "2026-08-27T13:00:00+00:00",
            "location": "",
        })
        event, status = extract_proposed_event(processed(), raw(), client=client)
        assert event is None
        assert status == ProposedEventStatus.NONE
