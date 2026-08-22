"""CLI: run scheduling-intent detection + calendar context/suggestions
against a hardcoded fake scheduling email. No real Gmail ingestion needed —
run.py owns replacing FAKE_SCHEDULING_EMAIL with real data later.

Usage (from the project root):
    python -m calendar.cli
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from models.schema import RawEmail, ReadStatus

from .context import get_calendar_context
from .scheduling_intent import is_scheduling_related
from .suggest import suggest_available_slots

FAKE_SCHEDULING_EMAIL = RawEmail(
    email_id="fake-001",
    thread_id="thread-fake-001",
    sender="alex@example.com",
    subject="Quick sync this week?",
    body_text=(
        "Hey - are you free for a quick call sometime Wednesday or Thursday? "
        "Happy to work around your schedule, just let me know what time works."
    ),
    received_at=datetime.now(timezone.utc),
    read_status=ReadStatus.READ,
    headers={},
)


def main() -> None:
    email = FAKE_SCHEDULING_EMAIL
    scheduling = is_scheduling_related(email)

    print(f"Email subject: {email.subject!r}")
    print(f"is_scheduling_related: {scheduling}")

    if not scheduling:
        print("\nNot scheduling-related — no Calendar API call made.")
        return

    range_start = datetime.now(timezone.utc)
    range_end = range_start + timedelta(days=7)

    try:
        context = get_calendar_context(range_start, range_end)
    except FileNotFoundError as exc:
        print(f"\n[Calendar API not configured yet: {exc}]")
        sys.exit(1)

    print(f"\nCalendar context: {len(context.busy_blocks)} busy block(s) in the next 7 days")
    for block in context.busy_blocks:
        print(f"  busy: {block.start.isoformat()} -> {block.end.isoformat()}")

    slots = suggest_available_slots(
        duration_minutes=30,
        range_start=range_start,
        range_end=range_end,
        working_hours=(9, 18),
        calendar_context=context,
    )

    print(f"\nSuggested slots ({len(slots)}):")
    for slot in slots:
        print(f"  {slot.start.isoformat()} -> {slot.end.isoformat()}")


if __name__ == "__main__":
    main()
