"""suggest_available_slots — propose open working-hours slots from calendar data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from models.schema import CalendarContext, CalendarSlot

DEFAULT_MAX_SUGGESTIONS = 3


def suggest_available_slots(
    duration_minutes: int,
    range_start: datetime,
    range_end: datetime,
    working_hours: tuple[int, int] = (9, 18),
    calendar_context: Optional[CalendarContext] = None,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
) -> list[CalendarSlot]:
    """Return up to `max_suggestions` open slots of `duration_minutes` minutes,
    within `working_hours` on weekdays, between range_start and range_end,
    that don't overlap any busy block in `calendar_context`.

    Fetches calendar_context itself (live Calendar API call) if not given.
    """
    if calendar_context is None:
        from .context import get_calendar_context

        calendar_context = get_calendar_context(range_start, range_end)

    busy = sorted(calendar_context.busy_blocks, key=lambda b: b.start)
    duration = timedelta(minutes=duration_minutes)
    start_hour, end_hour = working_hours
    tz = range_start.tzinfo or timezone.utc

    suggestions: list[CalendarSlot] = []
    day = range_start.astimezone(tz).date()
    last_day = range_end.astimezone(tz).date()

    while day <= last_day and len(suggestions) < max_suggestions:
        if day.weekday() < 5:  # Monday-Friday only
            window_start = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=start_hour)
            window_end = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=end_hour)
            cursor = max(window_start, range_start)

            day_busy = [b for b in busy if b.end > window_start and b.start < window_end]
            for block in day_busy:
                if block.start - cursor >= duration:
                    suggestions.append(CalendarSlot(start=cursor, end=cursor + duration))
                    if len(suggestions) >= max_suggestions:
                        break
                cursor = max(cursor, block.end)

            if len(suggestions) < max_suggestions and window_end - cursor >= duration:
                suggestions.append(CalendarSlot(start=cursor, end=cursor + duration))

        day += timedelta(days=1)

    return suggestions[:max_suggestions]
