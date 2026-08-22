"""Interval math and timestamp parsing — the fiddly bits, in isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calendaring.timeutils import (
    UTC,
    ceil_to_granularity,
    ensure_aware,
    get_timezone,
    merge_slots,
    pad_slots,
    parse_rfc3339,
    subtract_busy,
    to_rfc3339,
    working_windows,
)
from models.schema import CalendarSlot


def dt(day, hour=0, minute=0, tz=UTC):
    return datetime(2026, 8, day, hour, minute, tzinfo=tz)


def slot(start_hour, end_hour, day=24):
    return CalendarSlot(dt(day, start_hour), dt(day, end_hour))


class TestParseRfc3339:
    def test_zulu_suffix(self):
        assert parse_rfc3339("2026-08-24T09:00:00Z") == dt(24, 9)

    def test_explicit_offset(self):
        parsed = parse_rfc3339("2026-08-24T09:00:00-07:00")
        assert parsed.utcoffset() == timedelta(hours=-7)

    def test_all_day_date_becomes_midnight(self):
        assert parse_rfc3339("2026-08-24") == dt(24, 0)

    def test_fractional_seconds_beyond_microseconds(self):
        """Python 3.9's fromisoformat rejects nanosecond precision."""
        parsed = parse_rfc3339("2026-08-24T09:00:00.123456789Z")
        assert parsed.microsecond == 123456

    def test_round_trip(self):
        assert parse_rfc3339(to_rfc3339(dt(24, 9))) == dt(24, 9)

    def test_naive_input_gets_a_timezone(self):
        assert ensure_aware(datetime(2026, 8, 24, 9)).tzinfo is UTC

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_rfc3339("not a timestamp")


class TestGetTimezone:
    def test_known_zone(self):
        assert get_timezone("America/Los_Angeles") is not None

    def test_unknown_zone_falls_back_to_utc(self):
        """A bad tz must degrade, not crash — see graceful-degradation rule."""
        assert get_timezone("Mars/Olympus_Mons") is UTC

    def test_none_falls_back_to_utc(self):
        assert get_timezone(None) is UTC


class TestMergeSlots:
    def test_overlapping_blocks_merge(self):
        merged = merge_slots([slot(9, 11), slot(10, 12)])
        assert merged == [slot(9, 12)]

    def test_adjacent_blocks_merge(self):
        assert merge_slots([slot(9, 10), slot(10, 11)]) == [slot(9, 11)]

    def test_disjoint_blocks_stay_separate(self):
        assert merge_slots([slot(9, 10), slot(13, 14)]) == [slot(9, 10), slot(13, 14)]

    def test_fully_contained_block_is_absorbed(self):
        assert merge_slots([slot(9, 17), slot(10, 11)]) == [slot(9, 17)]

    def test_unsorted_input(self):
        assert merge_slots([slot(13, 14), slot(9, 10)]) == [slot(9, 10), slot(13, 14)]

    def test_zero_length_blocks_dropped(self):
        assert merge_slots([slot(9, 9)]) == []

    def test_empty(self):
        assert merge_slots([]) == []


class TestSubtractBusy:
    def test_no_busy_leaves_whole_window(self):
        assert subtract_busy(slot(9, 17), []) == [slot(9, 17)]

    def test_middle_block_splits_window(self):
        assert subtract_busy(slot(9, 17), [slot(12, 13)]) == [slot(9, 12), slot(13, 17)]

    def test_block_covering_window_leaves_nothing(self):
        assert subtract_busy(slot(9, 17), [slot(8, 18)]) == []

    def test_leading_block(self):
        assert subtract_busy(slot(9, 17), [slot(8, 10)]) == [slot(10, 17)]

    def test_trailing_block(self):
        assert subtract_busy(slot(9, 17), [slot(16, 18)]) == [slot(9, 16)]

    def test_block_outside_window_ignored(self):
        assert subtract_busy(slot(9, 17), [slot(20, 21)]) == [slot(9, 17)]

    def test_multiple_blocks(self):
        free = subtract_busy(slot(9, 17), [slot(10, 11), slot(13, 14)])
        assert free == [slot(9, 10), slot(11, 13), slot(14, 17)]


class TestPadSlots:
    def test_buffer_grows_both_sides(self):
        padded = pad_slots([slot(10, 11)], 15)
        assert padded[0].start == dt(24, 9, 45)
        assert padded[0].end == dt(24, 11, 15)

    def test_zero_buffer_is_identity(self):
        assert pad_slots([slot(10, 11)], 0) == [slot(10, 11)]


class TestCeilToGranularity:
    @pytest.mark.parametrize(
        "minute,expected", [(0, 0), (1, 15), (14, 15), (15, 15), (16, 30), (46, 0)]
    )
    def test_rounds_up_to_quarter_hour(self, minute, expected):
        result = ceil_to_granularity(dt(24, 10, minute), 15)
        assert result.minute == expected

    def test_already_aligned_is_unchanged(self):
        assert ceil_to_granularity(dt(24, 10, 30), 15) == dt(24, 10, 30)

    def test_rounds_on_local_clock_for_half_hour_offset_zones(self):
        """India is UTC+5:30 — rounding in UTC would land on :30, not :00."""
        tz = get_timezone("Asia/Kolkata")
        value = datetime(2026, 8, 24, 10, 7, tzinfo=tz)
        assert ceil_to_granularity(value, 15).minute == 15


class TestWorkingWindows:
    def test_one_window_per_weekday(self):
        windows = working_windows(dt(24), dt(27), (9, 17), UTC)
        assert len(windows) == 3  # Mon, Tue, Wed
        assert all(w.start.hour == 9 and w.end.hour == 17 for w in windows)

    def test_weekends_excluded_by_default(self):
        # Aug 22-23 2026 is Sat-Sun.
        assert working_windows(dt(22), dt(24), (9, 17), UTC) == []

    def test_weekends_included_when_asked(self):
        windows = working_windows(dt(22), dt(24), (9, 17), UTC, include_weekends=True)
        assert len(windows) == 2

    def test_window_clipped_to_range(self):
        """A range starting mid-morning must not resurrect the earlier hours."""
        windows = working_windows(dt(24, 11), dt(24, 15), (9, 17), UTC)
        assert windows == [CalendarSlot(dt(24, 11), dt(24, 15))]

    def test_hours_are_local_not_utc(self):
        tz = get_timezone("America/Los_Angeles")
        windows = working_windows(dt(24), dt(26), (9, 17), tz)
        assert all(w.start.astimezone(tz).hour == 9 for w in windows)
        # 9am PDT is 16:00 UTC — the bug this test exists to catch.
        assert windows[0].start.astimezone(UTC).hour == 16

    def test_end_hour_24_is_midnight(self):
        windows = working_windows(dt(24), dt(25), (9, 24), UTC)
        assert windows[0].end == dt(25, 0)

    def test_range_ending_before_working_hours_yields_nothing(self):
        assert working_windows(dt(24, 6), dt(24, 8), (9, 17), UTC) == []
