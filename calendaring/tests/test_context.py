"""get_calendar_context against a scripted service — never the network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calendaring import config, context
from calendaring.tests.fakes import FakeCalendarService, busy, http_error
from calendaring.timeutils import UTC
from models.schema import CalendarContext, CalendarSlot

START = datetime(2026, 8, 24, tzinfo=UTC)
END = START + timedelta(days=7)


@pytest.fixture(autouse=True)
def _clear_tz_cache():
    context.clear_timezone_cache()
    yield
    context.clear_timezone_cache()


def event(**overrides):
    base = {
        "id": "e1",
        "summary": "Standup",
        "status": "confirmed",
        "start": {"dateTime": "2026-08-24T09:00:00Z"},
        "end": {"dateTime": "2026-08-24T09:30:00Z"},
        "organizer": {"email": "me@example.com"},
        "attendees": [{"email": "a@example.com"}, {"email": "b@example.com"}],
        "htmlLink": "https://calendar.example/e1",
    }
    base.update(overrides)
    return base


class TestFetchBusyBlocks:
    def test_parses_and_merges_busy_periods(self):
        service = FakeCalendarService(
            freebusy_responses=[busy(
                ("2026-08-24T09:00:00Z", "2026-08-24T11:00:00Z"),
                ("2026-08-24T10:00:00Z", "2026-08-24T12:00:00Z"),
            )]
        )
        blocks = context.fetch_busy_blocks(service, START, END)
        assert blocks == [CalendarSlot(datetime(2026, 8, 24, 9, tzinfo=UTC),
                                       datetime(2026, 8, 24, 12, tzinfo=UTC))]

    def test_sends_the_requested_window_and_calendars(self):
        service = FakeCalendarService()
        context.fetch_busy_blocks(service, START, END, ["primary", "team@example.com"])
        body = service.freebusy_calls[0]
        assert body["timeMin"].startswith("2026-08-24")
        assert [item["id"] for item in body["items"]] == ["primary", "team@example.com"]

    def test_per_calendar_errors_degrade_gracefully(self):
        """One unreadable calendar must not sink the whole window."""
        service = FakeCalendarService(freebusy_responses=[{
            "calendars": {
                "primary": {"busy": [{"start": "2026-08-24T09:00:00Z",
                                      "end": "2026-08-24T10:00:00Z"}]},
                "gone@example.com": {"errors": [{"reason": "notFound"}], "busy": []},
            }
        }])
        assert len(context.fetch_busy_blocks(service, START, END)) == 1

    def test_unparseable_period_is_skipped_not_fatal(self):
        service = FakeCalendarService(freebusy_responses=[{
            "calendars": {"primary": {"busy": [
                {"start": "garbage", "end": "also garbage"},
                {"start": "2026-08-24T09:00:00Z", "end": "2026-08-24T10:00:00Z"},
            ]}}
        }])
        assert len(context.fetch_busy_blocks(service, START, END)) == 1

    def test_empty_response(self):
        assert context.fetch_busy_blocks(FakeCalendarService(), START, END) == []

    def test_too_many_calendars_is_truncated_not_rejected(self):
        service = FakeCalendarService()
        ids = ["c{0}".format(i) for i in range(config.MAX_FREEBUSY_CALENDARS + 5)]
        context.fetch_busy_blocks(service, START, END, ids)
        assert len(service.freebusy_calls[0]["items"]) == config.MAX_FREEBUSY_CALENDARS

    def test_rate_limit_is_retried(self):
        service = FakeCalendarService(freebusy_responses=[
            http_error(429), busy(("2026-08-24T09:00:00Z", "2026-08-24T10:00:00Z"))
        ])
        assert len(context.fetch_busy_blocks(service, START, END)) == 1

    def test_permission_error_propagates(self):
        """A bad scope must surface, not hide behind retries."""
        service = FakeCalendarService(freebusy_responses=[http_error(403, "insufficientPermissions")])
        with pytest.raises(Exception):
            context.fetch_busy_blocks(service, START, END)


class TestFetchEvents:
    def test_normalizes_an_event(self):
        service = FakeCalendarService(events_responses=[{"items": [event()]}])
        result = context.fetch_events(service, START, END)[0]
        assert result["event_id"] == "e1"
        assert result["summary"] == "Standup"
        assert result["start"] == datetime(2026, 8, 24, 9, tzinfo=UTC)
        assert result["all_day"] is False
        assert result["attendee_count"] == 2
        assert result["calendar_id"] == "primary"

    def test_all_day_event_flagged(self):
        service = FakeCalendarService(events_responses=[{"items": [event(
            start={"date": "2026-08-26"}, end={"date": "2026-08-27"})]}])
        result = context.fetch_events(service, START, END)[0]
        assert result["all_day"] is True
        assert result["start"] == datetime(2026, 8, 26, tzinfo=UTC)

    def test_cancelled_events_dropped(self):
        service = FakeCalendarService(events_responses=[
            {"items": [event(status="cancelled"), event(id="e2")]}
        ])
        assert [e["event_id"] for e in context.fetch_events(service, START, END)] == ["e2"]

    def test_recurrence_is_expanded_by_the_api(self):
        service = FakeCalendarService()
        context.fetch_events(service, START, END)
        assert service.events_calls[0]["singleEvents"] is True
        assert service.events_calls[0]["orderBy"] == "startTime"

    def test_pagination_follows_next_page_token(self):
        service = FakeCalendarService(events_responses=[
            {"items": [event(id="e1")], "nextPageToken": "p2"},
            {"items": [event(id="e2")]},
        ])
        ids = [e["event_id"] for e in context.fetch_events(service, START, END)]
        assert ids == ["e1", "e2"]
        assert service.events_calls[1]["pageToken"] == "p2"

    def test_missing_title_gets_a_placeholder(self):
        service = FakeCalendarService(events_responses=[{"items": [event(summary=None)]}])
        assert context.fetch_events(service, START, END)[0]["summary"] == "(no title)"

    def test_unreadable_calendar_is_skipped_not_fatal(self):
        service = FakeCalendarService(events_responses=[http_error(404, "notFound")])
        assert context.fetch_events(service, START, END) == []

    def test_events_sorted_by_start(self):
        service = FakeCalendarService(events_responses=[{"items": [
            event(id="late", start={"dateTime": "2026-08-26T09:00:00Z"},
                  end={"dateTime": "2026-08-26T10:00:00Z"}),
            event(id="early"),
        ]}])
        ids = [e["event_id"] for e in context.fetch_events(service, START, END)]
        assert ids == ["early", "late"]


class TestGetCalendarContext:
    def test_returns_a_populated_context(self):
        service = FakeCalendarService(
            freebusy_responses=[busy(("2026-08-24T09:00:00Z", "2026-08-24T10:00:00Z"))],
            events_responses=[{"items": [event()]}],
        )
        result = context.get_calendar_context(START, END, service=service)
        assert isinstance(result, CalendarContext)
        assert result.range_start == START and result.range_end == END
        assert len(result.busy_blocks) == 1
        assert len(result.existing_events) == 1

    def test_does_not_set_suggested_slots(self):
        """The frozen contract says that is suggest_available_slots' job."""
        result = context.get_calendar_context(START, END, service=FakeCalendarService())
        assert result.suggested_slots == []

    def test_naive_datetimes_are_made_aware(self):
        result = context.get_calendar_context(
            datetime(2026, 8, 24), datetime(2026, 8, 31), service=FakeCalendarService()
        )
        assert result.range_start.tzinfo is not None

    def test_inverted_range_rejected(self):
        with pytest.raises(ValueError):
            context.get_calendar_context(END, START, service=FakeCalendarService())

    def test_defaults_to_the_configured_window(self):
        result = context.get_calendar_context(service=FakeCalendarService(), days=14)
        assert (result.range_end - result.range_start) == timedelta(days=14)


class TestGetCalendarTimezone:
    def test_reads_the_calendars_own_timezone(self):
        service = FakeCalendarService(calendars_responses=[{"timeZone": "America/New_York"}])
        assert context.get_calendar_timezone(service) == "America/New_York"

    def test_result_is_cached(self):
        """40 scheduling emails must not mean 40 calendars.get calls."""
        service = FakeCalendarService(calendars_responses=[{"timeZone": "Europe/Berlin"}])
        for _ in range(5):
            context.get_calendar_timezone(service)
        assert len(service.calendars_calls) == 1

    def test_env_override_wins_without_any_api_call(self, monkeypatch):
        monkeypatch.setattr(config, "TIMEZONE", "Asia/Tokyo")
        service = FakeCalendarService()
        assert context.get_calendar_timezone(service) == "Asia/Tokyo"
        assert service.calendars_calls == []

    def test_lookup_failure_degrades_to_utc(self):
        service = FakeCalendarService(calendars_responses=[http_error(404, "notFound")])
        assert context.get_calendar_timezone(service) == "UTC"

    def test_no_service_is_utc(self):
        assert context.get_calendar_timezone(None) == "UTC"
