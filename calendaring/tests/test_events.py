"""`create_event` is the only function in the repo that writes to Google
Calendar. These tests exercise it purely against the fake service — no
network — and check the two contracts callers rely on: success sets
`google_event_id`, and an ordinary API failure is returned (not raised) with
`error` set, so the approve endpoint can persist a FAILED status without a
try/except of its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from calendaring.events import create_event
from calendaring.tests.fakes import FakeCalendarService, http_error
from models.schema import ProposedEvent

UTC = timezone.utc
NOW = datetime(2026, 8, 27, 14, tzinfo=UTC)


def proposed(**overrides) -> ProposedEvent:
    defaults = dict(
        title="Sync with Dana",
        start=NOW,
        end=NOW + timedelta(minutes=30),
        attendees=["dana@example.com"],
        location="Room 2",
    )
    defaults.update(overrides)
    return ProposedEvent(**defaults)


class TestSuccess:
    def test_inserts_the_event_and_returns_the_id(self):
        service = FakeCalendarService(insert_responses=[{"id": "gcal-abc123"}])
        result = create_event(proposed(), service=service, timezone_name="UTC")
        assert result.google_event_id == "gcal-abc123"
        assert result.error is None

    def test_returns_a_new_object_rather_than_mutating_the_input(self):
        service = FakeCalendarService(insert_responses=[{"id": "gcal-abc123"}])
        original = proposed()
        result = create_event(original, service=service, timezone_name="UTC")
        assert original.google_event_id is None
        assert result is not original

    def test_request_body_carries_title_time_attendees_location(self):
        service = FakeCalendarService(insert_responses=[{"id": "x"}])
        create_event(proposed(), service=service, calendar_id="primary", timezone_name="UTC")
        body = service.insert_calls[0]["body"]
        assert body["summary"] == "Sync with Dana"
        assert body["start"]["dateTime"] == NOW.isoformat()
        assert body["end"]["dateTime"] == (NOW + timedelta(minutes=30)).isoformat()
        assert body["attendees"] == [{"email": "dana@example.com"}]
        assert body["location"] == "Room 2"
        assert service.insert_calls[0]["calendarId"] == "primary"

    def test_no_attendees_or_location_are_omitted_from_the_body(self):
        service = FakeCalendarService(insert_responses=[{"id": "x"}])
        create_event(
            proposed(attendees=[], location=None), service=service, timezone_name="UTC"
        )
        body = service.insert_calls[0]["body"]
        assert "attendees" not in body
        assert "location" not in body


class TestFailure:
    def test_api_error_is_returned_not_raised(self):
        # A non-retryable reason (same precedent as test_context.py's
        # test_permission_error_propagates) so the test doesn't sleep through
        # retry backoff.
        service = FakeCalendarService(insert_responses=[http_error(403, "insufficientPermissions")])
        result = create_event(proposed(), service=service, timezone_name="UTC")
        assert result.google_event_id is None
        assert result.error is not None

    def test_failure_does_not_mutate_the_input(self):
        service = FakeCalendarService(insert_responses=[http_error(403, "insufficientPermissions")])
        original = proposed()
        create_event(original, service=service, timezone_name="UTC")
        assert original.error is None
