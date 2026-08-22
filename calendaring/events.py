"""Writing a single event to Google Calendar.

The only place in the repo that calls `events().insert(...)`. Called from
exactly one place — the review UI's approve endpoint (`api/main.py`) — after
an explicit user action. Nothing here is reachable from the pipeline; see
`PHASES.md` Phase 1B and `calendaring/config.py`'s scope comment for why that
separation matters (the write OAuth scope was granted early, but the write
code path stayed gated behind human approval).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional

from models.schema import ProposedEvent

from . import config
from .retry import with_retry
from .timeutils import get_timezone, to_rfc3339

log = logging.getLogger(__name__)


def _event_body(proposed: ProposedEvent, timezone_name: str) -> dict:
    body = {
        "summary": proposed.title,
        "start": {"dateTime": to_rfc3339(proposed.start), "timeZone": timezone_name},
        "end": {"dateTime": to_rfc3339(proposed.end), "timeZone": timezone_name},
    }
    if proposed.attendees:
        body["attendees"] = [{"email": email} for email in proposed.attendees]
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
        created = with_retry(
            lambda: service.events().insert(calendarId=calendar_id, body=body).execute(),
            description="events.insert({0})".format(calendar_id),
            max_retries=config.MAX_RETRIES,
        )
    except Exception as exc:
        log.warning("Could not create calendar event %r: %s", proposed.title, exc)
        return replace(proposed, google_event_id=None, error=str(exc))

    return replace(proposed, google_event_id=created.get("id"), error=None)
