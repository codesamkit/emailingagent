"""Free/busy blocks and existing events for a date window.

Read-only: `freebusy.query` and `events.list`, nothing else.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from models.schema import CalendarContext, CalendarSlot

from . import config
from .retry import with_retry
from .timeutils import (
    UTC,
    default_range,
    ensure_aware,
    get_timezone,
    merge_slots,
    parse_rfc3339,
    to_rfc3339,
)

log = logging.getLogger(__name__)

# Calendar timezones effectively never change mid-run, and looking one up
# costs a round trip. Cached per calendar id so that pulling context for 40
# scheduling emails does not make 40 identical calendars.get calls.
_TIMEZONE_CACHE: Dict[str, str] = {}


def clear_timezone_cache() -> None:
    """Drop the cached calendar timezones (used by tests)."""
    _TIMEZONE_CACHE.clear()


def get_calendar_timezone(service=None, calendar_id: str = "primary") -> str:
    """The IANA timezone working hours should be interpreted in.

    Resolution order: an explicit `CALENDAR_TIMEZONE` override > the
    calendar's own setting > UTC. The API lookup is best-effort — a calendar
    whose timezone can't be read should degrade to UTC, not abort scheduling.
    """
    if config.TIMEZONE:
        return config.TIMEZONE
    if calendar_id in _TIMEZONE_CACHE:
        return _TIMEZONE_CACHE[calendar_id]
    if service is None:
        return "UTC"
    try:
        from .calendar_auth import get_calendar_metadata

        name = get_calendar_metadata(service, calendar_id).get("timeZone") or "UTC"
    except Exception as exc:
        log.warning(
            "Could not read timezone for calendar %r (%s); assuming UTC",
            calendar_id,
            exc,
        )
        name = "UTC"
    _TIMEZONE_CACHE[calendar_id] = name
    return name


def fetch_busy_blocks(
    service,
    range_start: datetime,
    range_end: datetime,
    calendar_ids: Optional[Sequence[str]] = None,
) -> List[CalendarSlot]:
    """Merged busy intervals across every requested calendar.

    Uses `freebusy.query` rather than deriving busy-ness from events.list:
    it already accounts for declined invitations, `transparency: transparent`
    ("free") events, and all-day holds, which reimplementing would get wrong.
    """
    ids = list(calendar_ids or config.CALENDAR_IDS)
    if len(ids) > config.MAX_FREEBUSY_CALENDARS:
        log.warning(
            "freebusy.query accepts at most %d calendars; ignoring %d extra",
            config.MAX_FREEBUSY_CALENDARS,
            len(ids) - config.MAX_FREEBUSY_CALENDARS,
        )
        ids = ids[: config.MAX_FREEBUSY_CALENDARS]

    body = {
        "timeMin": to_rfc3339(range_start),
        "timeMax": to_rfc3339(range_end),
        "timeZone": "UTC",
        "items": [{"id": cid} for cid in ids],
    }
    response = with_retry(
        lambda: service.freebusy().query(body=body).execute(),
        description="freebusy.query",
    )

    blocks: List[CalendarSlot] = []
    for cid, payload in (response.get("calendars") or {}).items():
        for error in payload.get("errors") or []:
            # One unreadable calendar (deleted, unshared) should not sink the
            # whole window — report it and use what did come back.
            log.warning(
                "freebusy error for calendar %r: %s",
                cid,
                error.get("reason", error),
            )
        for period in payload.get("busy") or []:
            try:
                blocks.append(
                    CalendarSlot(
                        start=parse_rfc3339(period["start"]),
                        end=parse_rfc3339(period["end"]),
                    )
                )
            except (KeyError, ValueError) as exc:
                log.warning("Skipping unparseable busy period %r (%s)", period, exc)
    return merge_slots(blocks)


def _normalize_event(raw: Dict[str, Any], calendar_id: str) -> Dict[str, Any]:
    """Flatten a Calendar event into the plain dict the schema expects.

    `CalendarContext.existing_events` is typed `list[dict]` in the frozen
    contract, so a stable, small subset is emitted rather than the full API
    resource — downstream tracks get predictable keys and no surprise payload
    size in an LLM prompt.
    """
    start_raw = raw.get("start") or {}
    end_raw = raw.get("end") or {}
    all_day = "date" in start_raw and "dateTime" not in start_raw

    def _when(section: Dict[str, Any]) -> Optional[datetime]:
        value = section.get("dateTime") or section.get("date")
        if not value:
            return None
        tz = get_timezone(section.get("timeZone")) if section.get("timeZone") else UTC
        try:
            return parse_rfc3339(value, default_tz=tz)
        except ValueError:
            return None

    attendees = raw.get("attendees") or []
    return {
        "event_id": raw.get("id"),
        "calendar_id": calendar_id,
        "summary": raw.get("summary") or "(no title)",
        "start": _when(start_raw),
        "end": _when(end_raw),
        "all_day": all_day,
        "status": raw.get("status"),
        "organizer": (raw.get("organizer") or {}).get("email"),
        "attendee_count": len(attendees),
        "location": raw.get("location"),
        "html_link": raw.get("htmlLink"),
    }


def fetch_events(
    service,
    range_start: datetime,
    range_end: datetime,
    calendar_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Existing events in the window, recurrences expanded, oldest first."""
    ids = list(calendar_ids or config.CALENDAR_IDS)
    events: List[Dict[str, Any]] = []

    for cid in ids:
        page_token = None
        while True:
            token = page_token

            def call():
                return (
                    service.events()
                    .list(
                        calendarId=cid,
                        timeMin=to_rfc3339(range_start),
                        timeMax=to_rfc3339(range_end),
                        # Expand recurring series into individual instances —
                        # a weekly standup must occupy each of its slots, not
                        # just the series' first one.
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=config.EVENTS_PAGE_SIZE,
                        pageToken=token,
                    )
                    .execute()
                )

            try:
                response = with_retry(
                    call, description="events.list({0})".format(cid)
                )
            except Exception as exc:
                log.warning("Could not list events for calendar %r (%s)", cid, exc)
                break

            for raw in response.get("items") or []:
                if raw.get("status") == "cancelled":
                    continue
                events.append(_normalize_event(raw, cid))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    events.sort(key=lambda e: (e["start"] is None, e["start"] or range_start))
    return events


def get_calendar_context(
    range_start: Optional[datetime] = None,
    range_end: Optional[datetime] = None,
    *,
    service=None,
    calendar_ids: Optional[Sequence[str]] = None,
    days: Optional[int] = None,
) -> CalendarContext:
    """Free/busy blocks + existing events for the given window.

    Matches the frozen signature in `interfaces/README.md`; `service`,
    `calendar_ids`, and `days` are additive keyword-only extras (dependency
    injection for tests, multi-calendar support, and a shorthand for the
    default N-day lookahead). Positional use is unchanged.

    Does not set `suggested_slots` — that is `suggest_available_slots`' job.
    """
    if range_start is None or range_end is None:
        window_start, window_end = default_range(days)
        range_start = range_start or window_start
        range_end = range_end or window_end
    range_start, range_end = ensure_aware(range_start), ensure_aware(range_end)
    if range_end <= range_start:
        raise ValueError(
            "range_end must be after range_start (got {0} -> {1})".format(
                range_start.isoformat(), range_end.isoformat()
            )
        )

    if service is None:
        from .calendar_auth import get_calendar_service

        service = get_calendar_service()

    return CalendarContext(
        range_start=range_start,
        range_end=range_end,
        busy_blocks=fetch_busy_blocks(service, range_start, range_end, calendar_ids),
        existing_events=fetch_events(service, range_start, range_end, calendar_ids),
        suggested_slots=[],
    )
