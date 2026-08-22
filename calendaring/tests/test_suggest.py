"""suggest_available_slots — the scheduling logic users actually see."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from calendaring import config, context as context_module, suggest
from calendaring.suggest import suggest_available_slots, suggest_for_context
from calendaring.tests.fakes import ExplodingService, FakeCalendarService, busy
from calendaring.timeutils import UTC, get_timezone
from models.schema import CalendarContext, CalendarSlot

# Mon 24 Aug 2026 through Sun 30 Aug.
MONDAY = datetime(2026, 8, 24, tzinfo=UTC)
# Well before any working hour, so MIN_LEAD_MINUTES never interferes.
NOW = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_tz_cache():
    context_module.clear_timezone_cache()
    yield
    context_module.clear_timezone_cache()


def ctx(*busy_pairs, days=7, start=MONDAY):
    return CalendarContext(
        range_start=start,
        range_end=start + timedelta(days=days),
        busy_blocks=[CalendarSlot(s, e) for s, e in busy_pairs],
        existing_events=[],
    )


def at(day_offset, hour, minute=0):
    return MONDAY + timedelta(days=day_offset, hours=hour, minutes=minute)


def call(context, **kwargs):
    kwargs.setdefault("timezone_name", "UTC")
    kwargs.setdefault("now", NOW)
    return suggest_available_slots(30, context=context, **kwargs)


class TestBasics:
    def test_empty_calendar_offers_the_start_of_each_working_day(self):
        slots = call(ctx())
        assert len(slots) == 3
        assert [s.start for s in slots] == [at(0, 9), at(1, 9), at(2, 9)]

    def test_slot_length_matches_requested_duration(self):
        slots = suggest_available_slots(45, context=ctx(), timezone_name="UTC", now=NOW)
        assert all((s.end - s.start) == timedelta(minutes=45) for s in slots)

    def test_returns_at_most_max_slots(self):
        assert len(call(ctx(), max_slots=2)) == 2

    def test_default_is_two_to_three_slots(self):
        """Phase 1B asks for 2-3 proposals, not a wall of options."""
        assert 2 <= len(call(ctx())) <= 3

    def test_busy_morning_pushes_the_slot_later(self):
        slots = call(ctx((at(0, 9), at(0, 12))))
        assert slots[0].start == at(0, 12)

    def test_fully_booked_day_is_skipped(self):
        slots = call(ctx((at(0, 9), at(0, 17))))
        assert slots[0].start == at(1, 9)

    def test_overlapping_busy_blocks_are_merged_before_fitting(self):
        slots = call(ctx((at(0, 9), at(0, 12)), (at(0, 11), at(0, 14))))
        assert slots[0].start == at(0, 14)

    def test_gap_between_meetings_is_used(self):
        slots = call(ctx((at(0, 9), at(0, 11)), (at(0, 12), at(0, 17))))
        assert slots[0].start == at(0, 11)

    def test_gap_too_small_for_duration_is_skipped(self):
        """A 20-minute hole cannot host a 30-minute meeting."""
        slots = call(ctx((at(0, 9), at(0, 12)), (at(0, 12, 20), at(0, 17))))
        assert slots[0].start != at(0, 12)
        assert slots[0].start == at(1, 9)


class TestNoSlotsAvailable:
    def test_returns_empty_list_when_nothing_fits(self):
        """The contract says [] — the caller owns the fallback wording."""
        blocked = [(at(d, 0), at(d, 23, 59)) for d in range(7)]
        assert call(ctx(*blocked)) == []

    def test_duration_longer_than_the_working_day(self):
        slots = suggest_available_slots(
            600, context=ctx(), timezone_name="UTC", now=NOW
        )
        assert slots == []

    def test_zero_or_negative_duration_rejected(self):
        with pytest.raises(ValueError):
            suggest_available_slots(0, context=ctx())
        with pytest.raises(ValueError):
            suggest_available_slots(-30, context=ctx())


class TestWorkingHours:
    def test_slots_never_fall_outside_working_hours(self):
        slots = call(ctx(), working_hours=(10, 12))
        assert all(10 <= s.start.hour and s.end.hour <= 12 for s in slots)

    def test_custom_working_hours_are_honored(self):
        slots = call(ctx(), working_hours=(13, 18))
        assert slots[0].start == at(0, 13)

    def test_busy_time_outside_working_hours_is_irrelevant(self):
        slots = call(ctx((at(0, 5), at(0, 8))))
        assert slots[0].start == at(0, 9)


class TestWeekends:
    def test_weekends_skipped_by_default(self):
        """Fri 28 Aug -> next candidate is Mon 31 Aug, not Sat 29."""
        friday = datetime(2026, 8, 28, tzinfo=UTC)
        slots = suggest_available_slots(
            30, context=ctx(days=5, start=friday), timezone_name="UTC",
            now=datetime(2026, 8, 28, 0, tzinfo=UTC),
        )
        # Fri 28 -> Mon 31 -> Tue 1 Sep. Sat 29 and Sun 30 are never offered.
        assert [s.start.weekday() for s in slots] == [4, 0, 1]
        assert all(s.start.weekday() < 5 for s in slots)

    def test_weekends_included_when_requested(self):
        friday = datetime(2026, 8, 28, tzinfo=UTC)
        slots = suggest_available_slots(
            30, context=ctx(days=5, start=friday), timezone_name="UTC",
            now=datetime(2026, 8, 28, 0, tzinfo=UTC), include_weekends=True,
        )
        assert [s.start.weekday() for s in slots] == [4, 5, 6]


class TestTimezone:
    def test_working_hours_are_local_not_utc(self):
        """The bug this whole tz path exists to prevent: 9am != 9am UTC."""
        tz = get_timezone("America/Los_Angeles")
        slots = call(ctx(), timezone_name="America/Los_Angeles")
        assert all(s.start.astimezone(tz).hour == 9 for s in slots)
        assert slots[0].start.astimezone(UTC).hour == 16

    def test_busy_blocks_in_utc_are_compared_correctly_against_local_hours(self):
        # 16:00-20:00 UTC == 09:00-13:00 PDT, so the morning is gone.
        tz = get_timezone("America/Los_Angeles")
        slots = call(ctx((at(0, 16), at(0, 20))), timezone_name="America/Los_Angeles")
        assert slots[0].start.astimezone(tz).hour == 13

    def test_half_hour_offset_zone_lands_on_local_clock_boundaries(self):
        tz = get_timezone("Asia/Kolkata")
        slots = call(ctx(), timezone_name="Asia/Kolkata")
        assert all(s.start.astimezone(tz).minute in (0, 15, 30, 45) for s in slots)

    def test_unknown_timezone_degrades_to_utc(self):
        assert call(ctx(), timezone_name="Mars/Olympus_Mons")[0].start == at(0, 9)


class TestOnePerDay:
    def test_at_most_one_slot_per_day(self):
        """Three half-hours on one afternoon is one option, not three."""
        slots = call(ctx())
        assert len({s.start.date() for s in slots}) == len(slots)

    def test_spreads_across_days_even_with_a_wide_open_calendar(self):
        slots = call(ctx(), max_slots=3)
        assert [s.start.date().day for s in slots] == [24, 25, 26]


class TestLeadTimeAndAlignment:
    def test_never_proposes_a_slot_starting_immediately(self):
        now = at(0, 9, 5)
        slots = suggest_available_slots(30, context=ctx(), timezone_name="UTC", now=now)
        assert slots[0].start >= now + timedelta(minutes=config.MIN_LEAD_MINUTES)

    def test_start_times_snap_to_the_granularity_grid(self):
        slots = call(ctx((at(0, 9), at(0, 10, 7))))
        assert slots[0].start == at(0, 10, 15)

    def test_lead_time_can_push_past_today(self):
        now = at(0, 16, 45)
        slots = suggest_available_slots(30, context=ctx(), timezone_name="UTC", now=now)
        assert slots[0].start.date() == at(1, 0).date()


class TestApiUsage:
    def test_provided_context_avoids_a_second_api_call(self):
        """Track C already has calendar_context; re-fetching it is waste."""
        slots = suggest_available_slots(
            30, context=ctx(), service=ExplodingService(),
            timezone_name="UTC", now=NOW,
        )
        assert slots

    def test_fetches_its_own_context_when_none_is_given(self):
        service = FakeCalendarService(
            freebusy_responses=[busy(("2026-08-24T09:00:00Z", "2026-08-24T12:00:00Z"))],
            calendars_responses=[{"timeZone": "UTC"}],
        )
        slots = suggest_available_slots(
            30, MONDAY, MONDAY + timedelta(days=3), service=service, now=NOW
        )
        assert service.freebusy_calls
        assert slots[0].start == at(0, 12)


class TestSuggestForContext:
    def test_fills_suggested_slots_in_place(self):
        context = ctx()
        returned = suggest_for_context(
            context, 30, timezone_name="UTC", now=NOW, service=ExplodingService()
        )
        assert returned is context
        assert len(context.suggested_slots) == 3
