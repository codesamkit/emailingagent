"""`calendaring/events.py` is the only module in the repo that writes to
Google Calendar. These tests exercise create/update/delete purely against
the fake service — no network. `create_event`'s contract: success sets
`google_event_id`, and an ordinary API failure is returned (not raised) with
`error` set, so the approve endpoint can persist a FAILED status without a
try/except of its own. `update_event`/`delete_event` raise instead — see
`events.py`'s module docstring for why.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calendaring.events import create_event, delete_event, update_event
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

    def test_no_invitation_emails_are_sent_by_default(self):
        """Attendees are extracted from email text by a model, so mailing an
        invitation to whatever it produced is a side effect nobody asked for."""
        service = FakeCalendarService(insert_responses=[{"id": "x"}])
        create_event(proposed(), service=service, timezone_name="UTC")
        assert service.insert_calls[0]["sendUpdates"] == "none"

    def test_attendees_that_are_not_addresses_are_dropped_from_the_body(self):
        """Rows persisted before extraction validated addresses still hold
        display names, and one bad entry makes Google reject the whole insert."""
        service = FakeCalendarService(insert_responses=[{"id": "x"}])
        create_event(
            proposed(attendees=["Ronith", "Dana Reed <dana@example.com>"]),
            service=service,
            timezone_name="UTC",
        )
        assert service.insert_calls[0]["body"]["attendees"] == [{"email": "dana@example.com"}]

    def test_body_omits_attendees_when_none_are_valid(self):
        service = FakeCalendarService(insert_responses=[{"id": "x"}])
        create_event(proposed(attendees=["Ronith"]), service=service, timezone_name="UTC")
        assert "attendees" not in service.insert_calls[0]["body"]

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


class TestUpdateEvent:
    """Unlike create_event, update_event raises on failure — see the module
    docstring for why the two functions deliberately differ."""

    def test_only_sends_the_fields_given(self):
        service = FakeCalendarService()
        update_event("gcal-1", summary="Renamed", service=service)

        body = service.patch_calls[0]["body"]
        assert body == {"summary": "Renamed"}
        assert service.patch_calls[0]["eventId"] == "gcal-1"
        assert service.patch_calls[0]["calendarId"] == "primary"

    def test_rescheduling_sends_only_start_and_end(self):
        service = FakeCalendarService()
        update_event("gcal-1", start=NOW, end=NOW + timedelta(minutes=30), service=service, timezone_name="UTC")

        body = service.patch_calls[0]["body"]
        assert body == {
            "start": {"dateTime": NOW.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": (NOW + timedelta(minutes=30)).isoformat(), "timeZone": "UTC"},
        }

    def test_rejects_end_before_start_when_both_given(self):
        with pytest.raises(ValueError):
            update_event("gcal-1", start=NOW + timedelta(hours=1), end=NOW, service=FakeCalendarService())

    def test_returns_the_updated_event(self):
        service = FakeCalendarService(patch_responses=[{"id": "gcal-1", "summary": "Renamed"}])
        result = update_event("gcal-1", summary="Renamed", service=service)
        assert result["summary"] == "Renamed"

    def test_raises_on_api_error(self):
        service = FakeCalendarService(patch_responses=[http_error(403, "insufficientPermissions")])
        with pytest.raises(Exception):
            update_event("gcal-1", summary="Renamed", service=service)


class TestDeleteEvent:
    def test_deletes_the_event(self):
        service = FakeCalendarService()
        delete_event("gcal-1", service=service)
        assert service.delete_calls[0] == {"calendarId": "primary", "eventId": "gcal-1"}

    def test_swallows_already_gone(self):
        service = FakeCalendarService(delete_responses=[http_error(404)])
        delete_event("gcal-1", service=service)  # must not raise

    def test_swallows_410_gone(self):
        service = FakeCalendarService(delete_responses=[http_error(410)])
        delete_event("gcal-1", service=service)  # must not raise

    def test_reraises_other_errors(self):
        service = FakeCalendarService(delete_responses=[http_error(403, "insufficientPermissions")])
        with pytest.raises(Exception):
            delete_event("gcal-1", service=service)
