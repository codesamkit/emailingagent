"""Hardcoded sample emails and canned free/busy data.

Phase 1B is meant to be testable without real mail and without network
access, so the CLI's demo and the test suite both read their fixtures from
here rather than each defining their own (DRY, per CLAUDE.md).

Built on the frozen `models.schema.RawEmail`. The negative cases are not
padding: `NEWSLETTER_WEBINAR` and `RECEIPT` are the false positives a naive
keyword match produces, and they are here to keep the gate honest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from models.schema import CalendarSlot, RawEmail, ReadStatus

UTC = timezone.utc
_SENT = datetime(2026, 8, 21, 15, 30, tzinfo=UTC)


def _email(
    email_id: str,
    sender: str,
    subject: str,
    body: str,
    headers: Optional[Dict[str, str]] = None,
    read_status: ReadStatus = ReadStatus.READ,
) -> RawEmail:
    return RawEmail(
        email_id=email_id,
        thread_id="thread-{0}".format(email_id),
        sender=sender,
        recipients=["me@example.com"],
        subject=subject,
        body=body,
        received_at=_SENT,
        read_status=read_status,
        headers=dict(headers or {"From": sender, "To": "me@example.com"}),
    )


# --- Expected: scheduling related -----------------------------------------

DIRECT_ASK = _email(
    "sched-1",
    "Dana Reed <dana@example.com>",
    "Quick sync this week?",
    "Hi,\n\nAre you free for a 30 minute call sometime this week to go over "
    "the Q3 numbers? Mornings work best for me.\n\nThanks,\nDana",
)

RESCHEDULE = _email(
    "sched-2",
    "Priya Menon <priya@example.com>",
    "Re: Design review",
    "Something came up on my end — can we reschedule our design review? "
    "I'm around most of Thursday and Friday afternoon.\n\nPriya",
)

CALENDAR_INVITE = _email(
    "sched-3",
    "Marcus Hale <marcus@partner.example>",
    "Invitation: Roadmap planning @ Tue Aug 25, 2026 2pm - 3pm",
    "You have been invited to the following event.\n\nRoadmap planning\n"
    "Tuesday Aug 25, 2026 2:00pm - 3:00pm (PDT)",
    headers={
        "From": "Marcus Hale <marcus@partner.example>",
        "To": "me@example.com",
        "Content-Type": 'text/calendar; charset="UTF-8"; method=REQUEST',
    },
)

PROPOSED_TIME = _email(
    "sched-4",
    "Sam Okafor <sam@example.com>",
    "Onboarding call",
    "Does Wednesday at 2pm work for you? Happy to send a calendar invite "
    "once you confirm.\n\nSam",
)

VAGUE_CATCHUP = _email(
    "sched-5",
    "Lena Fischer <lena@example.com>",
    "Long time!",
    "It's been ages. Would love to grab coffee and catch up — let me know "
    "your availability over the next couple of weeks.\n\nLena",
)

SCHEDULING_SAMPLES: List[RawEmail] = [
    DIRECT_ASK,
    RESCHEDULE,
    CALENDAR_INVITE,
    PROPOSED_TIME,
    VAGUE_CATCHUP,
]

# --- Expected: NOT scheduling related -------------------------------------

RECEIPT = _email(
    "plain-1",
    "receipts@shop.example",
    "Your order #48211 has shipped",
    "Your order shipped today and should arrive Tuesday. Track it any time "
    "from your account.\n\nThis is an automated message — do not reply.",
    headers={
        "From": "receipts@shop.example",
        "To": "me@example.com",
        "Auto-Submitted": "auto-generated",
    },
)

# The trap: a bulk newsletter that name-drops a weekday, a clock time, and a
# meeting tool. Weak signals only, plus bulk headers -> must not trip the gate.
NEWSLETTER_WEBINAR = _email(
    "plain-2",
    "Acme Insights <news@acme.example>",
    "This week: our Tuesday webinar at 3pm",
    "Join us on Zoom next Tuesday at 3pm for a live session on platform "
    "trends. Can't make it? We'll email the recording.\n\nUnsubscribe any time.",
    headers={
        "From": "Acme Insights <news@acme.example>",
        "To": "subscribers@acme.example",
        "List-Unsubscribe": "<https://acme.example/u/1>",
        "Precedence": "bulk",
    },
)

PROJECT_QUESTION = _email(
    "plain-3",
    "Tom Alvarez <tom@example.com>",
    "Migration script question",
    "Did the migration script get merged? I want to make sure the rollback "
    "path is covered before we tag the release.\n\nTom",
)

NON_SCHEDULING_SAMPLES: List[RawEmail] = [RECEIPT, NEWSLETTER_WEBINAR, PROJECT_QUESTION]

ALL_SAMPLES: List[RawEmail] = SCHEDULING_SAMPLES + NON_SCHEDULING_SAMPLES

SAMPLES_BY_ID: Dict[str, RawEmail] = {e.email_id: e for e in ALL_SAMPLES}


# --- Canned calendar data (offline mode) -----------------------------------

def demo_busy_blocks(range_start: datetime) -> List[CalendarSlot]:
    """A plausibly busy week, relative to the window start.

    Anchored to `range_start` rather than to fixed dates so `--offline` keeps
    producing sensible output no matter when it is run.
    """
    day = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        # Day 0: booked solid 09:00-12:00 and 13:00-15:00 UTC.
        CalendarSlot(day + timedelta(hours=9), day + timedelta(hours=12)),
        CalendarSlot(day + timedelta(hours=13), day + timedelta(hours=15)),
        # Day 1: a single standup.
        CalendarSlot(day + timedelta(days=1, hours=9), day + timedelta(days=1, hours=9, minutes=30)),
        # Day 2: all-day offsite.
        CalendarSlot(day + timedelta(days=2), day + timedelta(days=3)),
        # Day 3: afternoon interviews.
        CalendarSlot(day + timedelta(days=3, hours=13), day + timedelta(days=3, hours=17)),
    ]


def demo_events(range_start: datetime) -> List[Dict]:
    day = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        {
            "event_id": "evt-1",
            "calendar_id": "primary",
            "summary": "Q3 planning",
            "start": day + timedelta(hours=9),
            "end": day + timedelta(hours=12),
            "all_day": False,
            "status": "confirmed",
            "organizer": "dana@example.com",
            "attendee_count": 6,
            "location": "Room 4B",
            "html_link": None,
        },
        {
            "event_id": "evt-2",
            "calendar_id": "primary",
            "summary": "Daily standup",
            "start": day + timedelta(days=1, hours=9),
            "end": day + timedelta(days=1, hours=9, minutes=30),
            "all_day": False,
            "status": "confirmed",
            "organizer": "me@example.com",
            "attendee_count": 4,
            "location": None,
            "html_link": None,
        },
        {
            "event_id": "evt-3",
            "calendar_id": "primary",
            "summary": "Team offsite",
            "start": day + timedelta(days=2),
            "end": day + timedelta(days=3),
            "all_day": True,
            "status": "confirmed",
            "organizer": "ops@example.com",
            "attendee_count": 22,
            "location": "Pier 27",
            "html_link": None,
        },
        {
            "event_id": "evt-4",
            "calendar_id": "primary",
            "summary": "Candidate interviews",
            "start": day + timedelta(days=3, hours=13),
            "end": day + timedelta(days=3, hours=17),
            "all_day": False,
            "status": "confirmed",
            "organizer": "recruiting@example.com",
            "attendee_count": 3,
            "location": None,
            "html_link": None,
        },
    ]
