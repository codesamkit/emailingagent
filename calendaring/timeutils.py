"""Pure time/interval helpers — no network, no I/O, no Google types.

Quarantined here because the fiddly parts of this module (RFC 3339 offsets,
all-day dates, overlapping busy blocks, timezone-correct working hours) are
where scheduling bugs actually live, and they are far easier to test in
isolation than through a Calendar API client.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

from models.schema import CalendarSlot

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - only on <3.9, which this repo isn't
    ZoneInfo = None  # type: ignore[assignment]

UTC = timezone.utc


def utcnow() -> datetime:
    """Timezone-aware 'now'. Centralized so tests can monkeypatch one symbol."""
    return datetime.now(UTC)


def get_timezone(name: Optional[str]):
    """Resolve an IANA timezone name, falling back to UTC.

    A bad or unavailable tz database entry must not take down slot
    suggestion — proposing UTC-based times is a degraded result, crashing is
    a broken one.
    """
    if not name or ZoneInfo is None:
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def ensure_aware(value: datetime, default_tz=UTC) -> datetime:
    """Attach a timezone to a naive datetime rather than guessing later.

    Naive datetimes are the single most common source of off-by-hours bugs
    here, so they are normalized at every boundary instead of being allowed
    to flow inward.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=default_tz)
    return value


def parse_rfc3339(value: str, default_tz=UTC) -> datetime:
    """Parse a Google timestamp (`dateTime`) or all-day date (`date`).

    Google emits both `2026-08-24T09:00:00Z` and `2026-08-24T09:00:00-07:00`;
    `datetime.fromisoformat` on Python 3.9 rejects the trailing `Z`, so it is
    rewritten to an explicit offset first.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # All-day events carry a bare date; midnight in the target tz is the
    # correct interpretation of "the day starts here".
    if len(text) == 10 and text.count("-") == 2:
        parsed = datetime.combine(date.fromisoformat(text), time.min)
        return parsed.replace(tzinfo=default_tz)
    # Fractional seconds beyond microsecond precision are not parseable by
    # fromisoformat on 3.9; truncating is lossless for scheduling purposes.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        for char in tail:
            if char.isdigit():
                digits += char
            else:
                tail = tail[len(digits):]
                break
        else:
            tail = ""
        text = "{0}.{1}{2}".format(head, (digits + "000000")[:6], tail)
    return ensure_aware(datetime.fromisoformat(text), default_tz)


def to_rfc3339(value: datetime) -> str:
    """Format for the Calendar API, always with an explicit offset."""
    return ensure_aware(value).isoformat()


def default_range(days: Optional[int] = None, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """A `[now, now + days)` window in UTC — the usual 7-14 day lookahead."""
    from . import config

    span = config.DEFAULT_WINDOW_DAYS if days is None else days
    start = ensure_aware(now) if now is not None else utcnow()
    return start, start + timedelta(days=span)


def merge_slots(slots: Iterable[CalendarSlot]) -> List[CalendarSlot]:
    """Collapse overlapping/adjacent intervals into a minimal disjoint set.

    Busy blocks arrive per-calendar and routinely overlap (a meeting on the
    work calendar and a 'blocked' hold on the personal one). Subtracting them
    from a free window one at a time would double-count; merging first makes
    the free-gap computation a single pass.
    """
    ordered = sorted(
        (s for s in slots if s.end > s.start),
        key=lambda s: (ensure_aware(s.start), ensure_aware(s.end)),
    )
    merged: List[CalendarSlot] = []
    for slot in ordered:
        start, end = ensure_aware(slot.start), ensure_aware(slot.end)
        if merged and start <= ensure_aware(merged[-1].end):
            last = merged[-1]
            if end > ensure_aware(last.end):
                merged[-1] = CalendarSlot(start=last.start, end=end)
        else:
            merged.append(CalendarSlot(start=start, end=end))
    return merged


def subtract_busy(
    window: CalendarSlot, busy: Sequence[CalendarSlot]
) -> List[CalendarSlot]:
    """The parts of `window` left free once `busy` is removed.

    `busy` must already be merged (see `merge_slots`).
    """
    free: List[CalendarSlot] = []
    cursor = ensure_aware(window.start)
    window_end = ensure_aware(window.end)

    for block in busy:
        block_start = ensure_aware(block.start)
        block_end = ensure_aware(block.end)
        if block_end <= cursor:
            continue
        if block_start >= window_end:
            break
        if block_start > cursor:
            free.append(CalendarSlot(start=cursor, end=min(block_start, window_end)))
        cursor = max(cursor, block_end)
        if cursor >= window_end:
            return free

    if cursor < window_end:
        free.append(CalendarSlot(start=cursor, end=window_end))
    return free


def pad_slots(slots: Iterable[CalendarSlot], minutes: int) -> List[CalendarSlot]:
    """Grow each interval by `minutes` on both sides (a back-to-back buffer)."""
    if minutes <= 0:
        return [CalendarSlot(start=ensure_aware(s.start), end=ensure_aware(s.end)) for s in slots]
    delta = timedelta(minutes=minutes)
    return [
        CalendarSlot(start=ensure_aware(s.start) - delta, end=ensure_aware(s.end) + delta)
        for s in slots
    ]


def ceil_to_granularity(value: datetime, minutes: int) -> datetime:
    """Round a start time up onto a clean :00/:15/:30 boundary.

    Rounding is done in the value's own timezone, so a calendar on a
    half-hour offset (India, Nepal) still lands on local clock boundaries
    rather than on UTC ones.
    """
    if minutes <= 0:
        return value
    step = timedelta(minutes=minutes)
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = value - midnight
    remainder = elapsed % step
    if remainder:
        value = value + (step - remainder)
    return value.replace(second=0, microsecond=0)


def working_windows(
    range_start: datetime,
    range_end: datetime,
    working_hours: Tuple[int, int],
    tz,
    include_weekends: bool = False,
) -> List[CalendarSlot]:
    """One window per day covering `working_hours` *in the calendar's tz*.

    Evaluating working hours in the local calendar timezone rather than UTC
    is the whole point: '9-5' expressed in UTC puts a Los Angeles user's
    suggested slots at 2am.
    """
    start_hour, end_hour = working_hours
    local_start = ensure_aware(range_start).astimezone(tz)
    local_end = ensure_aware(range_end).astimezone(tz)

    windows: List[CalendarSlot] = []
    day = local_start.date()
    last_day = local_end.date()
    while day <= last_day:
        if include_weekends or day.weekday() < 5:  # Mon-Fri
            day_start = datetime.combine(day, time(hour=start_hour), tzinfo=tz)
            # hour=24 is not representable; express it as midnight next day.
            if end_hour == 24:
                day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
            else:
                day_end = datetime.combine(day, time(hour=end_hour), tzinfo=tz)
            window_start = max(day_start, local_start)
            window_end = min(day_end, local_end)
            if window_end > window_start:
                windows.append(CalendarSlot(start=window_start, end=window_end))
        day += timedelta(days=1)
    return windows
