"""Turning calendar availability into reply-outline bullets.

Separate from `outline.py` because this is the deterministic half: the slots
a reply offers must come from real free/busy data, never from an LLM's guess
at when the user is free. `outline.py` asks the model for the *substance* of
the reply; this module supplies the *times*, already decided.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from calendaring.suggest import suggest_available_slots
from models.schema import CalendarContext, CalendarSlot, ProcessedEmail

log = logging.getLogger(__name__)

# Kept short: an outline is meant to be scanned, and three options is already
# the upper end of what a recipient will actually consider.
MAX_SLOTS_IN_OUTLINE = 3

NO_SLOTS_BULLET = (
    "Note: no open slots found in the next week — offer to look further out "
    "or suggest the recipient propose a time"
)

# Distinct from NO_SLOTS_BULLET on purpose. "Nothing is free" and "we could
# not read your calendar" are different facts, and only one of them is safe to
# imply in a reply. Collapsing them would put a statement in the outline that
# nobody actually verified.
CALENDAR_UNAVAILABLE_BULLET = (
    "Note: your calendar could not be checked for this email — confirm your "
    "own availability before replying"
)


def format_slot(slot: CalendarSlot, tz=None) -> str:
    """A slot as a human would write it in an email."""
    start = slot.start.astimezone(tz) if tz else slot.start
    end = slot.end.astimezone(tz) if tz else slot.end
    same_day = start.date() == end.date()
    return "{0} {1}-{2}".format(
        start.strftime("%a %d %b"),
        start.strftime("%H:%M"),
        end.strftime("%H:%M") if same_day else end.strftime("%a %d %b %H:%M"),
    )


def availability_bullet(slots: List[CalendarSlot], tz=None) -> str:
    """One bullet offering the given slots, or the no-availability fallback."""
    if not slots:
        return NO_SLOTS_BULLET
    return "Offer these times (from your calendar, already checked for conflicts): {0}".format(
        "; ".join(format_slot(slot, tz) for slot in slots[:MAX_SLOTS_IN_OUTLINE])
    )


def slots_for(
    processed: ProcessedEmail,
    duration_minutes: Optional[int] = None,
    **kwargs,
) -> List[CalendarSlot]:
    """Suggested slots for a scheduling email, reusing already-fetched context.

    The pipeline populates `processed.calendar_context` before drafting runs,
    so this passes it straight through rather than triggering a second
    `freebusy.query` per email. A context that already carries suggestions is
    used as-is.

    Degrades to [] on any calendar failure: a reply outline missing its times
    is recoverable, a crashed pipeline run is not.
    """
    context: Optional[CalendarContext] = processed.calendar_context
    if context is None:
        return []
    if context.suggested_slots:
        return list(context.suggested_slots)
    try:
        return suggest_available_slots(
            duration_minutes, context=context, max_slots=MAX_SLOTS_IN_OUTLINE, **kwargs
        )
    except Exception as exc:
        log.warning(
            "Could not suggest slots for %s (%s); outline will omit times",
            processed.email_id,
            exc,
        )
        return []


def fold_slots_into_outline(
    bullets: List[str],
    processed: ProcessedEmail,
    duration_minutes: Optional[int] = None,
    tz=None,
    **kwargs,
) -> List[str]:
    """Append the availability bullet when this email is scheduling-related.

    Appended after the model's bullets rather than merged into them, so the
    times remain traceably ours and cannot be reworded by the model.

    Three outcomes, kept distinct:
      - calendar read, slots fit     -> offer them
      - calendar read, nothing fits  -> say so
      - calendar not read at all     -> say *that*, not "nothing fits"
    """
    if not processed.is_scheduling_related:
        return bullets
    if processed.calendar_context is None:
        return list(bullets) + [CALENDAR_UNAVAILABLE_BULLET]
    slots = slots_for(processed, duration_minutes, **kwargs)
    return list(bullets) + [availability_bullet(slots, tz)]
