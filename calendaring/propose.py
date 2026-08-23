"""Extracting a proposed meeting from a scheduling-related email.

Mirrors `drafting/outline.py`'s shape: a hard code-level gate before any LLM
client is touched, then one JSON-schema-constrained call. The gate here is
`processed.is_scheduling_related` (already computed by the pipeline's
scheduling stage) rather than re-detecting intent — see
`calendaring/scheduling_intent.py`.

This module only *extracts*; it never calls the Calendar API and never
writes anything. Creating the event on Google Calendar is
`calendaring/events.py:create_event`, called only after explicit user
approval (see interfaces/README.md).
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Tuple

from models.schema import ProcessedEmail, ProposedEvent, ProposedEventStatus, RawEmail

from . import config
from .timeutils import ensure_aware

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 4000
DEFAULT_DURATION_MINUTES = 30

SYSTEM_PROMPT = (
    "You extract a single proposed MEETING from an email, for a calendar "
    "assistant that will create a real calendar event from what you return. "
    "Only extract a meeting if a SPECIFIC date and start time are pinned "
    "down — either the sender proposes one ('Thursday at 2pm', 'Aug 25, "
    "10-10:30am') or the email confirms a specific time that was already "
    "proposed. If the email only asks about availability with no candidate "
    "time settled ('let me know what works', 'are you free next week?'), "
    'set "found" to false and leave the other fields empty — do not guess a '
    "time. Never invent an attendee, time, or title that is not implied by "
    "the email. Interpret relative dates (\"Thursday\", \"next week\") "
    "relative to the email's received date, which is given to you, and "
    "interpret any bare clock time in the timezone given to you. Always "
    'return "start" and "end" as ISO 8601 timestamps with an explicit UTC '
    "offset (e.g. 2026-08-27T14:00:00-07:00), never a bare local time. "
    "Default the meeting length to {0} minutes if no duration is stated. "
    "Keep the title short and specific, e.g. 'Sync with Priya' — not just "
    "'Meeting'.".format(DEFAULT_DURATION_MINUTES)
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "title": {"type": "string", "maxLength": 200},
        "start": {"type": "string", "maxLength": 40},
        "end": {"type": "string", "maxLength": 40},
        "attendees": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 10,
        },
        "location": {"type": "string", "maxLength": 200},
    },
    "required": ["found", "title", "start", "end", "attendees", "location"],
    "additionalProperties": False,
}


def _default_model() -> str:
    from llm.client import model_for

    # Reuses the "outline" stage's routing: both are low-volume,
    # judgment-heavy calls, the same rationale drafting/expand.py uses.
    return model_for("outline")


def _get_default_client() -> Any:
    from llm.client import get_client

    return get_client("outline")


def _build_user_message(processed: ProcessedEmail, raw: RawEmail, tz_name: str) -> str:
    parts = [
        "Sender: {0}".format(raw.sender),
        "Subject: {0}".format(raw.subject),
        "Received at (UTC): {0}".format(raw.received_at.isoformat()),
        "Interpret bare clock times in this timezone: {0}".format(tz_name),
    ]
    if processed.summary:
        parts.append("Summary: {0}".format(processed.summary))
    parts.append("Body:\n{0}".format((raw.body or "")[:MAX_BODY_CHARS]))
    return "\n".join(parts)


def extract_proposed_event(
    processed: ProcessedEmail,
    raw: RawEmail,
    client: Optional[Any] = None,
    tz: Optional[str] = None,
) -> Tuple[Optional[ProposedEvent], ProposedEventStatus]:
    """Extract a proposed meeting from `raw`, or (None, NONE) if there isn't one.

    Gated on `processed.is_scheduling_related` — an email the scheduling gate
    didn't flag never reaches the LLM, the same "an `if` cannot drift" logic
    `drafting/outline.py` uses for its own eligibility gate.
    """
    if not processed.is_scheduling_related:
        return None, ProposedEventStatus.NONE

    tz_name = tz or config.TIMEZONE or "UTC"

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(processed, raw, tz_name)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)

    if not data.get("found"):
        log.debug("email_id=%s: no concrete meeting to extract", raw.email_id)
        return None, ProposedEventStatus.NONE

    try:
        start = ensure_aware(_parse_iso(data["start"]))
        end = ensure_aware(_parse_iso(data["end"]))
    except (KeyError, ValueError) as exc:
        log.warning("email_id=%s: unparseable extracted time (%s)", raw.email_id, exc)
        return None, ProposedEventStatus.NONE

    if end <= start:
        log.warning("email_id=%s: extracted end <= start; discarding", raw.email_id)
        return None, ProposedEventStatus.NONE

    title = (data.get("title") or "").strip() or "Meeting with {0}".format(raw.sender)
    attendees = _clean_attendees(data.get("attendees"), raw.sender)
    location = (data.get("location") or "").strip() or None

    proposed = ProposedEvent(
        title=title, start=start, end=end, attendees=attendees, location=location
    )
    log.debug("email_id=%s: extracted proposed event %r", raw.email_id, title)
    return proposed, ProposedEventStatus.SUGGESTED


def _parse_iso(value: str):
    from datetime import datetime

    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _clean_attendees(raw_attendees: Optional[List[str]], sender: str) -> List[str]:
    """Bare email addresses only, de-duplicated, first-appearance order.

    The model routinely answers with display names ("Ronith") and the sender
    arrives header-decorated ("Name <addr@host>"). Google's attendee list
    takes bare addresses and rejects the ENTIRE insert with
    "Invalid attendee email." if any one entry isn't one, so a single name
    used to sink the whole event. Anything without a parseable address is
    dropped rather than passed through.

    `find_addresses` is reused instead of adding another address parser —
    `context/normalize.py` makes the same call for the same reason.
    """
    from context.extract import find_addresses

    # The sender proposed or confirmed the meeting, so they belong on the
    # invite even if the model didn't repeat their address back.
    candidates = list(raw_attendees or [])
    if sender:
        candidates.append(sender)

    attendees: List[str] = []
    for candidate in candidates:
        for address in find_addresses(candidate or ""):
            if address not in attendees:
                attendees.append(address)
    return attendees
