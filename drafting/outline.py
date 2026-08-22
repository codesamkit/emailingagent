"""Reply-outline generation (Track C / drafting).

Two entry points:
  - generate_reply_outline: matches the frozen interfaces/README.md
    signature. Assumes the caller has already checked eligibility - it
    will happily call the LLM for any input it's given.
  - process_email_for_outline: the actual code-level gate. This is what
    the pipeline (and read-status-change handlers) should call - it
    decides eligibility BEFORE generate_reply_outline (and therefore
    before any LLM call) ever runs, per PHASES.md item 1.
"""

from __future__ import annotations

import os
from typing import Optional

from models.schema import CalendarContext, ProcessedEmail, ReadStatus, ReplyOutlineStatus

from .calendar_aware import fold_calendar_slots_into_outline

_ANTHROPIC_MODEL = "claude-sonnet-5"


def process_email_for_outline(processed: ProcessedEmail) -> ProcessedEmail:
    """Apply the read/no-reply gate, then generate (or clear) reply_outline
    in place on `processed`. Safe to call repeatedly - e.g. once at
    ingestion time and again whenever read_status flips to READ, which is
    how regeneration works (no separate "regenerate" function needed).
    """
    if processed.is_no_reply:
        # No-reply always wins, even if the email is manually marked read.
        processed.reply_outline = None
        processed.reply_outline_status = ReplyOutlineStatus.NOT_APPLICABLE
        return processed

    if processed.read_status != ReadStatus.READ:
        processed.reply_outline = None
        processed.reply_outline_status = ReplyOutlineStatus.NONE
        return processed

    processed.reply_outline = generate_reply_outline(processed, processed.calendar_context)
    processed.reply_outline_status = ReplyOutlineStatus.SUGGESTED
    return processed


def generate_reply_outline(
    processed: ProcessedEmail,
    calendar_context: Optional[CalendarContext],
) -> list[str]:
    """Generate a short bullet-point reply outline (not a full email) for
    an already-eligible email. Caller is responsible for the read/no-reply
    gate - see process_email_for_outline.
    """
    prompt = _build_prompt(processed)
    outline = _call_llm(prompt)

    if processed.is_scheduling_related and calendar_context is not None:
        outline = fold_calendar_slots_into_outline(outline, calendar_context)

    return outline


def _build_prompt(processed: ProcessedEmail) -> str:
    return (
        "Draft a short bullet-point outline (not a full email) for replying "
        "to this email. Each bullet should be a brief skeleton point, not "
        "prose - the user will expand it themselves.\n\n"
        f"From: {processed.sender}\n"
        f"Subject: {processed.subject}\n"
        f"Summary: {processed.summary or '(no summary available)'}\n"
    )


def _call_llm(prompt: str) -> list[str]:
    """LLM seam - isolated so tests can monkeypatch this single function
    instead of mocking a network client. Real implementation calls the
    Anthropic API.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    return [line.strip("-* ").strip() for line in text.splitlines() if line.strip()]
