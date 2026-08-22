"""Folds Track A's calendar-derived slot suggestions into a reply outline.

Kept separate from outline.py so the LLM prompt never has to guess
availability - real slots from get_calendar_context/suggest_available_slots
are formatted directly into a bullet instead.
"""

from __future__ import annotations

from models.schema import CalendarContext, CalendarSlot

MAX_SLOTS_IN_BULLET = 2


def fold_calendar_slots_into_outline(
    outline: list[str],
    calendar_context: CalendarContext,
) -> list[str]:
    """Return `outline` with a calendar-availability bullet appended, built
    from `calendar_context.suggested_slots`. No-op (returns outline
    unchanged) if there are no suggested slots to offer.
    """
    if not calendar_context.suggested_slots:
        return outline

    slot_text = _format_slots(calendar_context.suggested_slots[:MAX_SLOTS_IN_BULLET])
    bullet = f"Confirm you can make {slot_text} (per calendar availability)"
    return [*outline, bullet]


def _format_slots(slots: list[CalendarSlot]) -> str:
    formatted = [_format_slot(slot) for slot in slots]
    if len(formatted) == 1:
        return formatted[0]
    return " or ".join(formatted)


def _format_slot(slot: CalendarSlot) -> str:
    # e.g. "Wed 2pm"
    weekday = slot.start.strftime("%a")
    hour = slot.start.strftime("%I%p").lstrip("0").lower()
    return f"{weekday} {hour}"
