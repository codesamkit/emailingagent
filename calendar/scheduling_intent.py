"""is_scheduling_related — cheap keyword/pattern gate, no API or LLM calls.

Runs on every ingested email, so it deliberately avoids network/LLM calls —
only emails this returns True for should trigger a Calendar API lookup.
"""

from __future__ import annotations

import re

from models.schema import RawEmail

_KEYWORD_PATTERNS = [
    r"\bare you free\b",
    r"\bschedule (a|an|our)?\s*(call|meeting|chat|time)\b",
    r"\breschedul\w*\b",
    r"\blet'?s meet\b",
    r"\bbook (a|some)?\s*time\b",
    r"\bfree (on|this|next)\b",
    r"\bset up (a|an)?\s*(call|meeting|time)\b",
    r"\bavailab(le|ility)\b",
]
_KEYWORD_REGEX = re.compile("|".join(_KEYWORD_PATTERNS), re.IGNORECASE)

_SUBJECT_PREFIXES = ("invitation:", "invite:", "meeting request:")

_MEETING_WORD_REGEX = re.compile(r"\b(meet|call|chat|sync|catch up)\b", re.IGNORECASE)
_WEEKDAY_OR_TIME_REGEX = re.compile(
    r"\b(mon|tues?|wed(nes)?|thur?s?|fri|sat|sun)[a-z]*\b|\b\d{1,2}(:\d{2})?\s?(am|pm)\b",
    re.IGNORECASE,
)


def is_scheduling_related(email: RawEmail) -> bool:
    """Rule-based intent check: does this email look like it's about scheduling
    a meeting/call? True triggers a downstream Calendar API lookup; False doesn't.
    """
    subject_lower = email.subject.strip().lower()
    if subject_lower.startswith(_SUBJECT_PREFIXES):
        return True

    if "text/calendar" in email.headers.get("Content-Type", "").lower():
        return True

    combined = f"{email.subject}\n{email.body_text}"
    if _KEYWORD_REGEX.search(combined):
        return True

    # A bare date/time or weekday mention is weak evidence on its own (e.g.
    # "see you Tuesday!" in a newsletter) — only count it alongside a generic
    # meeting word, to keep the false-positive rate down.
    if _WEEKDAY_OR_TIME_REGEX.search(combined) and _MEETING_WORD_REGEX.search(combined):
        return True

    return False
