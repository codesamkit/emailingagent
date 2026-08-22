"""get_calendar_context — busy blocks derived from real calendar events."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from models.schema import CalendarContext, CalendarSlot

from .calendar_auth import get_calendar_service

DEFAULT_LOOKAHEAD_DAYS = 14
CALENDAR_ID = "primary"


def get_calendar_context(
    range_start: Optional[datetime] = None,
    range_end: Optional[datetime] = None,
    service: Any = None,
) -> CalendarContext:
    """Return busy blocks for [range_start, range_end] (default: now through
    DEFAULT_LOOKAHEAD_DAYS days out, UTC).

    Pulls from events.list rather than freebusy.query so that events marked
    "Free" (transparency=transparent) and events the user declined can be
    excluded — both would otherwise incorrectly show as busy time.
    """
    if range_start is None:
        range_start = datetime.now(timezone.utc)
    if range_end is None:
        range_end = range_start + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)

    service = service or get_calendar_service()

    events_resp = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=_to_rfc3339(range_start),
            timeMax=_to_rfc3339(range_end),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    busy_blocks: list[CalendarSlot] = []
    for event in events_resp.get("items", []):
        if event.get("transparency") == "transparent":
            continue
        if _self_declined(event):
            continue
        start = _parse_event_time(event.get("start", {}))
        end = _parse_event_time(event.get("end", {}))
        if start and end:
            busy_blocks.append(CalendarSlot(start=start, end=end))

    return CalendarContext(
        checked_range_start=range_start,
        checked_range_end=range_end,
        busy_blocks=busy_blocks,
        suggested_slots=[],
    )


def _to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_event_time(time_field: dict) -> Optional[datetime]:
    if "dateTime" in time_field:
        return datetime.fromisoformat(time_field["dateTime"])
    if "date" in time_field:  # all-day event
        d = date.fromisoformat(time_field["date"])
        return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    return None


def _self_declined(event: dict) -> bool:
    for attendee in event.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False
