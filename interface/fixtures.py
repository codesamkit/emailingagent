"""Demo ProcessedEmail fixtures for developing/reviewing the interface
before Phase 6's real pipeline output is wired in (per PHASES.md's Phase 7
scaffolding note). One example per state the UI needs to render: unread,
no-reply, a plain eligible outline, and a scheduling email with a
calendar-derived slot bullet.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models.schema import (
    CalendarContext,
    CalendarSlot,
    ImportanceLevel,
    ProcessedEmail,
    ReadStatus,
    ReplyOutlineStatus,
)

_NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


def demo_processed_emails() -> list[ProcessedEmail]:
    return [
        ProcessedEmail(
            email_id="demo-unread",
            thread_id="thread-unread",
            sender="jordan@example.com",
            subject="Draft budget doc for review",
            received_at=_NOW,
            read_status=ReadStatus.UNREAD,
            is_no_reply=False,
            importance_score=65.0,
            importance_level=ImportanceLevel.MEDIUM,
            importance_justification="Direct request, no deadline stated.",
            summary="Jordan shares a draft budget doc and asks for review comments.",
            reply_outline=None,
            reply_outline_status=ReplyOutlineStatus.NONE,
        ),
        ProcessedEmail(
            email_id="demo-no-reply",
            thread_id="thread-no-reply",
            sender="notifications@github.com",
            subject="[repo] New PR opened",
            received_at=_NOW,
            read_status=ReadStatus.READ,
            is_no_reply=True,
            no_reply_reason="sender local-part matches automated pattern",
            importance_score=20.0,
            importance_level=ImportanceLevel.LOW,
            importance_justification="Automated notification.",
            summary="Automated notification that a new pull request was opened.",
            reply_outline=None,
            reply_outline_status=ReplyOutlineStatus.NOT_APPLICABLE,
        ),
        ProcessedEmail(
            email_id="demo-eligible",
            thread_id="thread-eligible",
            sender="alex@example.com",
            subject="Can you take a look at the proposal?",
            received_at=_NOW,
            read_status=ReadStatus.READ,
            is_no_reply=False,
            importance_score=78.0,
            importance_level=ImportanceLevel.HIGH,
            importance_justification="Direct ask from a frequent correspondent.",
            summary="Alex asks for feedback on the attached proposal by Friday.",
            reply_outline=[
                "Acknowledge the request",
                "Confirm you'll review by Friday",
                "Ask if there's a specific section to focus on",
            ],
            reply_outline_status=ReplyOutlineStatus.SUGGESTED,
        ),
        ProcessedEmail(
            email_id="demo-stale",
            thread_id="thread-eligible",  # same thread as demo-eligible, received earlier
            sender="alex@example.com",
            subject="Re: Can you take a look at the proposal?",
            received_at=_NOW.replace(hour=8),
            read_status=ReadStatus.READ,
            is_no_reply=False,
            importance_score=70.0,
            importance_level=ImportanceLevel.HIGH,
            importance_justification="Direct ask from a frequent correspondent.",
            summary="Alex's first message asking for a proposal review.",
            reply_outline=["Acknowledge the request", "Confirm you'll review by Friday"],
            reply_outline_status=ReplyOutlineStatus.SUGGESTED,
        ),
        ProcessedEmail(
            email_id="demo-scheduling",
            thread_id="thread-scheduling",
            sender="sam@example.com",
            subject="Quick sync this week?",
            received_at=_NOW,
            read_status=ReadStatus.READ,
            is_no_reply=False,
            importance_score=82.0,
            importance_level=ImportanceLevel.HIGH,
            importance_justification="Direct scheduling request from a VIP.",
            summary="Sam wants to find time for a quick sync this week.",
            is_scheduling_related=True,
            calendar_context=CalendarContext(
                range_start=_NOW,
                range_end=_NOW,
                suggested_slots=[
                    CalendarSlot(start=_NOW.replace(hour=14), end=_NOW.replace(hour=14, minute=30)),
                ],
            ),
            reply_outline=[
                "Acknowledge the request",
                "Confirm you can make Wed 2pm (per calendar availability)",
            ],
            reply_outline_status=ReplyOutlineStatus.SUGGESTED,
        ),
    ]
