"""Cheap scheduling-intent check — no Calendar API call, no LLM call.

This is a *gate*, not a classifier: most inbox mail is not scheduling
related, so a fast keyword/MIME check decides whether paying for
`get_calendar_context` / `suggest_available_slots` is worth it at all.
Track C imports `is_scheduling_related` directly (see interfaces/README.md).

Tuned to favor recall over precision. A false positive costs one extra
freebusy query; a false negative means a scheduling email gets a reply
outline that guesses at availability, which is the exact failure this
project exists to avoid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# A single strong phrase is enough on its own; weak signals need corroboration.
STRONG_SCORE = 2
WEAK_SCORE = 1
THRESHOLD = 2

# Phrases that are essentially only ever written when arranging a time.
STRONG_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bare you (free|available)\b", "asks whether the recipient is free"),
    (r"\bwhen are you (free|available)\b", "asks when the recipient is free"),
    (r"\byour availability\b", "asks for availability"),
    (r"\blet me know (?:your |what )?(?:availability|times?|when)\b", "asks for times"),
    (r"\b(schedule|set ?up|book|arrange|line up)\s+(?:a|an|our|some)?\s*"
     r"(call|meeting|time|chat|sync|catch[- ]?up|appointment|slot|session|demo|interview)\b",
     "proposes scheduling a meeting"),
    (r"\bfind (?:a|some) time\b", "proposes finding a time"),
    (r"\b(re-?schedule|postpone|push back|move)\b[^.!?]{0,30}\b"
     r"(call|meeting|appointment|sync|chat|it|this)\b", "asks to reschedule"),
    (r"\bre-?schedule\b", "mentions rescheduling"),
    (r"\bdoes\b[^.!?]{0,25}\bwork for you\b", "proposes a specific time"),
    (r"\bwhat time works\b", "asks what time works"),
    (r"\bwhen works\b", "asks when works"),
    (r"\bwork(s)? for you\b", "proposes a time for confirmation"),
    (r"\bcalendar invite\b", "references a calendar invite"),
    (r"\bmeeting (request|invitation)\b", "is a meeting request"),
    (r"\bsend (?:you |over )?an? invite\b", "offers to send an invite"),
    (r"\bblock (?:off |out )?(?:some |an? )?"
     r"(?:time|hour|slot|\d+\s*(?:min(?:ute)?s?|hours?))\b", "proposes blocking time"),
    (r"\bpencil (?:you |it |us )?in\b", "proposes pencilling in a time"),
    (r"\bhop on a (call|zoom|meet)\b", "proposes a call"),
    (r"\bgrab (?:a )?(coffee|lunch|drink|time)\b", "proposes meeting up"),
)

# Individually ambiguous; meaningful in combination.
WEAK_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\b(meeting|call|sync|stand-?up|appointment|interview|demo)\b", "mentions a meeting"),
    (r"\bcatch[- ]?up\b", "mentions catching up"),
    (r"\bcalendar\b", "mentions a calendar"),
    (r"\bavailab(le|ility)\b", "mentions availability"),
    (r"\b(time ?slot|slot)\b", "mentions a slot"),
    (r"\b(mon|tues|wednes|thurs|fri|satur|sun)day\b", "names a weekday"),
    (r"\b(tomorrow|next week|this week|later today)\b", "names a relative day"),
    (r"\b\d{1,2}\s?(:\d{2})?\s?(am|pm)\b", "names a clock time"),
    (r"\b\d{1,2}:\d{2}\b", "names a clock time"),
    (r"\b(zoom|google meet|hangout|teams|webex)\b", "mentions a meeting tool"),
    (r"\b(morning|afternoon|evening)\b", "names a time of day"),
    (r"\b(free|available)\b", "mentions being free"),
)

# MIME/header markers of an actual calendar invitation. These are decisive.
_INVITE_CONTENT_TYPES = ("text/calendar", "application/ics")
_INVITE_METHODS = ("method=request", "method=reply", "method=cancel")

# Bulk-mail markers. A newsletter advertising a "webinar next Tuesday at 3pm"
# trips several weak patterns but is not the recipient arranging anything, so
# weak-only evidence is discounted when these are present. Strong phrases and
# real invites still win — automated mail does send genuine invites.
_BULK_HEADERS = ("List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted")

_COMPILED_STRONG = tuple((re.compile(p, re.IGNORECASE), why) for p, why in STRONG_PATTERNS)
_COMPILED_WEAK = tuple((re.compile(p, re.IGNORECASE), why) for p, why in WEAK_PATTERNS)

# Only the opening of a long thread is scanned: quoted history below it is
# mostly older mail, and matching it would make every reply in a long thread
# look like a fresh scheduling request.
MAX_BODY_CHARS = 2000


@dataclass
class SchedulingSignals:
    """Why the gate decided what it decided — surfaced by the CLI."""

    score: int = 0
    reasons: List[str] = field(default_factory=list)
    is_invite: bool = False
    is_bulk: bool = False

    @property
    def is_scheduling_related(self) -> bool:
        return self.is_invite or self.score >= THRESHOLD


def _body_of(email: Any) -> str:
    """Read the body regardless of which `RawEmail` shape was handed over.

    The frozen contract (`models/schema.py`) calls the field `body`, while
    `ingestion/models.py` currently calls it `body_text` and adds `snippet`.
    Until those two are reconciled, duck-typing here keeps this gate usable
    by both Track A's real ingestion output and Track C's schema fixtures.
    """
    for attribute in ("body", "body_text", "snippet"):
        value = getattr(email, attribute, None)
        if value:
            return str(value)
    return ""


def _headers_of(email: Any) -> Dict[str, str]:
    headers = getattr(email, "headers", None)
    return headers if isinstance(headers, dict) else {}


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup — header casing is not guaranteed."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _looks_like_invite(headers: Dict[str, str]) -> bool:
    content_type = (_header(headers, "Content-Type") or "").lower()
    if any(marker in content_type for marker in _INVITE_CONTENT_TYPES):
        return True
    if any(method in content_type.replace(" ", "") for method in _INVITE_METHODS):
        return True
    # Some senders only reveal the invite through the attachment filename.
    return ".ics" in content_type


def _is_bulk(headers: Dict[str, str]) -> bool:
    return any(_header(headers, name) for name in _BULK_HEADERS)


def scheduling_signals(email: Any) -> SchedulingSignals:
    """Full detail behind the gate's verdict, for debugging and the CLI."""
    headers = _headers_of(email)
    subject = str(getattr(email, "subject", "") or "")
    body = _body_of(email)[:MAX_BODY_CHARS]
    text = "{0}\n{1}".format(subject, body)

    signals = SchedulingSignals(
        is_invite=_looks_like_invite(headers), is_bulk=_is_bulk(headers)
    )
    if signals.is_invite:
        signals.reasons.append("carries a calendar invite (text/calendar)")

    strong_hits = 0
    for pattern, why in _COMPILED_STRONG:
        if pattern.search(text):
            strong_hits += 1
            signals.score += STRONG_SCORE
            signals.reasons.append(why)

    for pattern, why in _COMPILED_WEAK:
        if pattern.search(text):
            signals.score += WEAK_SCORE
            signals.reasons.append(why)

    # Discount bulk mail that only tripped weak patterns.
    if signals.is_bulk and not strong_hits and not signals.is_invite:
        signals.score = 0
        signals.reasons.append("discounted: bulk/automated headers, no explicit ask")

    return signals


def is_scheduling_related(email: Any) -> bool:
    """Cheap keyword/intent check — no Calendar API call.

    Gate that decides whether `get_calendar_context` /
    `suggest_available_slots` are worth calling at all for this email.
    Matches the frozen signature in `interfaces/README.md`.
    """
    return scheduling_signals(email).is_scheduling_related
