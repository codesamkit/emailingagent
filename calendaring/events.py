"""Writing to Google Calendar: creating, editing, and cancelling one event.

The only place in the repo that calls `events().insert/patch/delete(...)`.

`create_event` has two callers: the human-triggered approve route in
`api/main.py`, and — when `config.AUTO_ADD_EVENTS` is on — the batch pipeline
via `Pipeline._maybe_auto_add`. The pipeline path is newer: writes used to be
gated behind human action only (see `PHASES.md` Phase 1B and
`calendaring/config.py`'s scope comment), and setting CALENDAR_AUTO_ADD=0
restores that. `update_event`/`delete_event` remain human-triggered only.

Invitation emails are never sent unless a caller explicitly passes
`send_updates`: attendees come out of a model reading email text, so mailing
whatever it produced is a side effect the user never asked for.

`create_event` never raises — it returns the (unchanged) `ProposedEvent` with
`error` set, so the approve endpoint can persist a FAILED status without a
try/except of its own. `update_event`/`delete_event` are different: they
operate on an event that already exists (status APPROVED), called from
routes that already have their own try/except mapping errors to HTTP
responses, and a failed edit must NOT flip `proposed_event_status` away from
APPROVED — the event still exists on Calendar with its old data, only the
edit didn't take. So they raise like the rest of `calendaring/` does
(`context.py`, `suggest.py`), rather than repeating create_event's
swallow-and-report convention.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Sequence

from googleapiclient.errors import HttpError

from models.schema import ProposedEvent

from . import config
from .retry import with_retry
from .timeutils import get_timezone, to_rfc3339

log = logging.getLogger(__name__)


class SlotCheck(str, Enum):
    """What the calendar already holds in a proposed event's slot."""

    CLEAR = "clear"
    DUPLICATE = "duplicate"  # this same event is already there
    CONFLICT = "conflict"  # something else is there


def _normalized_title(title: Optional[str]) -> str:
    """Case-, whitespace- and trailing-punctuation-insensitive title key."""
    return " ".join((title or "").split()).casefold().rstrip(" .-–—:")


def check_slot(
    proposed: ProposedEvent, existing_events: Sequence[Dict[str, Any]]
) -> "tuple[SlotCheck, Optional[Dict[str, Any]]]":
    """Whether `proposed` can be created, and the event standing in its way.

    Exists because auto-add was idempotent against the DATABASE, not the
    calendar -- and the calendar is what actually holds the events. Once a
    record loses its APPROVED status (a wiped database, a reprocess, a
    re-ingest) the pipeline re-extracts the proposal, creates a second copy,
    and overwrites `google_event_id`, orphaning the first. Asking the calendar
    instead makes auto-add idempotent no matter what happens to the database.

    DUPLICATE requires an overlapping slot AND the same normalized title, so a
    re-extraction whose times shifted slightly is still recognized as the same
    meeting. Near-identical titles from different threads ("Lot 22B review" vs
    "Lot 22B technical review") are deliberately NOT matched: guessing two
    differently-worded meetings are the same risks silently dropping a real
    one, which is worse than a visible duplicate.

    All-day events are skipped -- they cover a whole day, so treating one as a
    conflict would block every proposal on that date.
    """
    if proposed.start is None or proposed.end is None:
        return SlotCheck.CLEAR, None

    target = _normalized_title(proposed.title)
    conflict = None
    for event in existing_events or []:
        if event.get("all_day"):
            continue
        start, end = event.get("start"), event.get("end")
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            continue
        if not (proposed.start < end and start < proposed.end):
            continue
        if _normalized_title(event.get("summary")) == target:
            # An exact match wins outright: nothing to create.
            return SlotCheck.DUPLICATE, event
        # Keep looking -- a duplicate later in the list beats a conflict.
        conflict = conflict or event
    if conflict is not None:
        return SlotCheck.CONFLICT, conflict
    return SlotCheck.CLEAR, None


def _event_body(proposed: ProposedEvent, timezone_name: str) -> dict:
    body = {
        "summary": proposed.title,
        "start": {"dateTime": to_rfc3339(proposed.start), "timeZone": timezone_name},
        "end": {"dateTime": to_rfc3339(proposed.end), "timeZone": timezone_name},
    }
    # Re-filtered here, not just at extraction: rows written before
    # `_clean_attendees` validated addresses still hold display names, and one
    # bad entry makes Google reject the whole insert.
    if proposed.attendees:
        from context.extract import find_addresses

        valid = []
        for candidate in proposed.attendees:
            for address in find_addresses(candidate or ""):
                if address not in valid:
                    valid.append(address)
        if valid:
            body["attendees"] = [{"email": address} for address in valid]
    if proposed.location:
        body["location"] = proposed.location
    if proposed.description:
        body["description"] = proposed.description
    return body


def create_event(
    proposed: ProposedEvent,
    service=None,
    calendar_id: str = "primary",
    timezone_name: Optional[str] = None,
    send_updates: str = "none",
) -> ProposedEvent:
    """Create `proposed` on Google Calendar and return the result.

    Returns a copy of `proposed` with `google_event_id` set on success, or
    with `error` set (and `google_event_id` left None) on ANY failure —
    an API error, a missing/expired token, missing client secrets — this
    function never raises, so a caller (the approve endpoint) can persist a
    FAILED status and show the user a message without a try/except of its
    own. Persistence and status transitions are the caller's job, not this
    one's.

    When `service` isn't injected, the credential lookup is non-interactive:
    this runs inside a synchronous HTTP request, and popping open a local
    browser OAuth consent flow to fill a missing token would hang that
    request instead of failing cleanly. Run `python -m calendaring.cli auth`
    out of band to mint a token before approving from the review UI.
    """
    try:
        if service is None:
            from .calendar_auth import get_calendar_service

            service = get_calendar_service(allow_interactive=False)

        if timezone_name is None:
            from .context import get_calendar_timezone

            timezone_name = get_calendar_timezone(service, calendar_id)
        # Validate against the tz database even though only the name is sent
        # to the API — an unresolvable name should surface here, not as a
        # confusing 400 from Google.
        get_timezone(timezone_name)

        body = _event_body(proposed, timezone_name)
        # sendUpdates defaults to "none": attendees are extracted from email
        # text by a model, so mailing a meeting invitation to whatever it
        # produced is a side effect the user never asked for. Callers that do
        # want invitations sent must say so explicitly.
        created = with_retry(
            lambda: service.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates=send_updates)
            .execute(),
            description="events.insert({0})".format(calendar_id),
            max_retries=config.MAX_RETRIES,
        )
    except Exception as exc:
        log.warning("Could not create calendar event %r: %s", proposed.title, exc)
        return replace(proposed, google_event_id=None, error=str(exc))

    return replace(proposed, google_event_id=created.get("id"), error=None)


def update_event(
    event_id: str,
    *,
    summary: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[Sequence[str]] = None,
    calendar_id: str = "primary",
    timezone_name: Optional[str] = None,
    service=None,
) -> Dict[str, Any]:
    """Rename, reschedule, or otherwise partially edit an existing event.

    Uses `events.patch`, not `events.update`, so an omitted field is left
    exactly as it was — the caller doesn't have to re-supply the whole event
    just to change its time. Raises on failure; see the module docstring for
    why that's a deliberate departure from `create_event`'s convention.
    """
    if start is not None and end is not None and end <= start:
        raise ValueError(
            "end must be after start (got {0} -> {1})".format(start.isoformat(), end.isoformat())
        )
    if service is None:
        from .calendar_auth import get_calendar_service

        service = get_calendar_service(allow_interactive=False)

    body: Dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary
    if start is not None or end is not None:
        if timezone_name is None:
            from .context import get_calendar_timezone

            timezone_name = get_calendar_timezone(service, calendar_id)
        if start is not None:
            body["start"] = {"dateTime": to_rfc3339(start), "timeZone": timezone_name}
        if end is not None:
            body["end"] = {"dateTime": to_rfc3339(end), "timeZone": timezone_name}
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if attendees is not None:
        body["attendees"] = [{"email": email} for email in attendees]

    return with_retry(
        lambda: service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute(),
        description="events.patch({0})".format(calendar_id),
        max_retries=config.MAX_RETRIES,
    )


def delete_event(
    event_id: str,
    *,
    calendar_id: str = "primary",
    service=None,
) -> None:
    """Cancel an event via `events.delete`.

    A 404 or 410 (already deleted/cancelled) is swallowed rather than
    raised: the caller's intent — "this event should not exist" — is already
    satisfied, and treating a double-cancel as an error would just make the
    UI retry into a wall. Any other failure raises, same as `update_event`.
    """
    if service is None:
        from .calendar_auth import get_calendar_service

        service = get_calendar_service(allow_interactive=False)

    try:
        with_retry(
            lambda: service.events().delete(calendarId=calendar_id, eventId=event_id).execute(),
            description="events.delete({0})".format(calendar_id),
            max_retries=config.MAX_RETRIES,
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (404, 410):
            log.info(
                "Event %r on calendar %r already gone (status %s)", event_id, calendar_id, status
            )
            return
        raise
