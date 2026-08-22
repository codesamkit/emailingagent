"""Propose a handful of open meeting slots from free/busy data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from models.schema import CalendarContext, CalendarSlot

from . import config
from .context import get_calendar_context, get_calendar_timezone
from .timeutils import (
    ceil_to_granularity,
    default_range,
    ensure_aware,
    get_timezone,
    merge_slots,
    pad_slots,
    subtract_busy,
    utcnow,
    working_windows,
)

log = logging.getLogger(__name__)


def _first_fit(
    window: CalendarSlot, duration: timedelta, granularity: int, earliest: datetime
) -> Optional[CalendarSlot]:
    """The earliest granularity-aligned slot of `duration` inside `window`."""
    start = max(ensure_aware(window.start), earliest)
    start = ceil_to_granularity(start, granularity)
    if start + duration <= ensure_aware(window.end):
        return CalendarSlot(start=start, end=start + duration)
    return None


def free_windows(
    context: CalendarContext,
    working_hours: Tuple[int, int],
    tz,
    include_weekends: bool = False,
    buffer_minutes: int = 0,
) -> List[CalendarSlot]:
    """Working-hour stretches in the context's range with nothing booked."""
    busy = merge_slots(pad_slots(context.busy_blocks, buffer_minutes))
    free: List[CalendarSlot] = []
    for window in working_windows(
        context.range_start, context.range_end, working_hours, tz, include_weekends
    ):
        free.extend(subtract_busy(window, busy))
    return free


def suggest_available_slots(
    duration_minutes: Optional[int] = None,
    range_start: Optional[datetime] = None,
    range_end: Optional[datetime] = None,
    working_hours: Optional[Tuple[int, int]] = None,
    *,
    context: Optional[CalendarContext] = None,
    service=None,
    calendar_ids: Optional[Sequence[str]] = None,
    max_slots: Optional[int] = None,
    include_weekends: Optional[bool] = None,
    timezone_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[CalendarSlot]:
    """2-3 proposed open slots within `working_hours`, given free/busy data.

    Matches the frozen signature in `interfaces/README.md`. The keyword-only
    extras are additive; the important one is `context`: Track C's pipeline
    has already populated `ProcessedEmail.calendar_context`, so passing it
    reuses that data instead of paying for a second `freebusy.query` per
    email. Omit it and this fetches its own.

    Returns [] if nothing fits — the caller decides the fallback messaging
    (e.g. an outline bullet saying "no open slots found this week").

    At most one slot per day is proposed. Offering three consecutive
    half-hours on the same afternoon is technically three options and
    practically one, so spreading them across days gives the recipient a real
    choice.
    """
    duration_minutes = (
        config.DEFAULT_DURATION_MINUTES if duration_minutes is None else duration_minutes
    )
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive, got {0}".format(duration_minutes))
    working_hours = config.WORKING_HOURS if working_hours is None else working_hours
    max_slots = config.DEFAULT_MAX_SLOTS if max_slots is None else max_slots
    include_weekends = (
        config.INCLUDE_WEEKENDS if include_weekends is None else include_weekends
    )

    if context is None:
        if range_start is None or range_end is None:
            window_start, window_end = default_range()
            range_start = range_start or window_start
            range_end = range_end or window_end
        context = get_calendar_context(
            range_start, range_end, service=service, calendar_ids=calendar_ids
        )

    tz = get_timezone(timezone_name or get_calendar_timezone(service))
    duration = timedelta(minutes=duration_minutes)
    # Never propose something starting in the next few minutes.
    earliest = (now or utcnow()) + timedelta(minutes=config.MIN_LEAD_MINUTES)

    suggestions: List[CalendarSlot] = []
    days_used = set()
    for window in free_windows(
        context, working_hours, tz, include_weekends, config.BUFFER_MINUTES
    ):
        day = ensure_aware(window.start).astimezone(tz).date()
        if day in days_used:
            continue
        slot = _first_fit(window, duration, config.SLOT_GRANULARITY_MINUTES, earliest)
        if slot is None:
            continue
        suggestions.append(slot)
        days_used.add(day)
        if len(suggestions) >= max_slots:
            break

    if not suggestions:
        log.info(
            "No %d-minute slot fits between %s and %s within working hours %s",
            duration_minutes,
            context.range_start.isoformat(),
            context.range_end.isoformat(),
            working_hours,
        )
    return suggestions


def suggest_for_context(
    context: CalendarContext,
    duration_minutes: Optional[int] = None,
    **kwargs,
) -> CalendarContext:
    """Fill `context.suggested_slots` in place and return it.

    Convenience for the pipeline: `get_calendar_context` deliberately leaves
    `suggested_slots` empty, and this is the one-liner that completes the
    record without a second API call.
    """
    context.suggested_slots = suggest_available_slots(
        duration_minutes, context=context, **kwargs
    )
    return context
