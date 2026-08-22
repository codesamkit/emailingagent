"""Dataclass -> JSON-safe dict conversion for the HTTP layer.

Kept separate from `models/schema.py` on purpose: the wire format is an API
concern that will change (field renames, added computed fields, pagination
envelopes) independently of the frozen internal contract. Coupling them would
make every UI tweak a change to a file three tracks share.

Keys are camelCase because the consumer is a JavaScript frontend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from models.schema import CalendarContext, CalendarSlot, ProcessedEmail


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def slot_to_json(slot: CalendarSlot) -> Dict[str, str]:
    return {"start": _iso(slot.start), "end": _iso(slot.end)}


def calendar_to_json(context: Optional[CalendarContext]) -> Optional[Dict[str, Any]]:
    if context is None:
        return None
    return {
        "rangeStart": _iso(context.range_start),
        "rangeEnd": _iso(context.range_end),
        "busyBlocks": [slot_to_json(s) for s in context.busy_blocks],
        "suggestedSlots": [slot_to_json(s) for s in context.suggested_slots],
        "eventCount": len(context.existing_events),
        "events": [
            {
                "summary": event.get("summary"),
                "start": _iso(event["start"]) if isinstance(event.get("start"), datetime) else event.get("start"),
                "end": _iso(event["end"]) if isinstance(event.get("end"), datetime) else event.get("end"),
                "allDay": event.get("all_day", False),
            }
            for event in context.existing_events
        ],
        "generatedAt": _iso(context.generated_at),
    }


def email_to_json(email: ProcessedEmail, include_calendar: bool = False) -> Dict[str, Any]:
    """One processed email as the frontend sees it.

    `outlineAvailable` is computed here rather than left to the client: the
    rule for whether a reply outline may exist (read AND not no-reply) is a
    correctness requirement, and re-deriving it in JavaScript would be a
    second place for it to go wrong.
    """
    from drafting.outline import is_eligible

    eligible, _ = is_eligible(email)
    return {
        "emailId": email.email_id,
        "threadId": email.thread_id,
        "sender": email.sender,
        "subject": email.subject or "",
        "receivedAt": _iso(email.received_at),
        "readStatus": email.read_status.value,
        "isNoReply": email.is_no_reply,
        "noReplyReason": email.no_reply_reason,
        "importanceScore": email.importance_score,
        "importanceLevel": email.importance_level.value if email.importance_level else None,
        "importanceJustification": email.importance_justification,
        "summary": email.summary,
        "isSchedulingRelated": email.is_scheduling_related,
        "hasCalendarContext": email.calendar_context is not None,
        "calendarContext": calendar_to_json(email.calendar_context) if include_calendar else None,
        "replyOutline": email.reply_outline,
        "replyOutlineStatus": email.reply_outline_status.value,
        "outlineEligible": eligible,
        "processedAt": _iso(email.processed_at),
    }


def emails_to_json(emails: List[ProcessedEmail]) -> List[Dict[str, Any]]:
    return [email_to_json(e) for e in emails]
