"""Reply-outline generation (Track C / drafting).

generate_reply_outline is the single entry point the frozen interface
contract (interfaces/README.md) specifies: it performs the read/no-reply
gate BEFORE any LLM call (per PHASES.md item 1) and returns the outline
plus its status rather than mutating its arguments, so callers (the
Phase 6 pipeline, read-status-change handlers, tests) decide what to do
with the result. Safe to call repeatedly - e.g. once at ingestion time
and again whenever read_status flips to READ, which is how regeneration
works (no separate "regenerate" function needed).
"""

from __future__ import annotations

import os

from models.schema import ProcessedEmail, RawEmail, ReadStatus, ReplyOutlineStatus

from .calendar_aware import fold_calendar_slots_into_outline

_ANTHROPIC_MODEL = "claude-sonnet-5"


def generate_reply_outline(
    processed: ProcessedEmail,
    raw: RawEmail,
) -> tuple[list[str] | None, ReplyOutlineStatus]:
    """Apply the read/no-reply gate, then generate a short bullet-point
    reply outline (not a full email) for an eligible email.
    """
    if processed.is_no_reply:
        # No-reply always wins, even if the email is manually marked read.
        return None, ReplyOutlineStatus.NOT_APPLICABLE

    if processed.read_status != ReadStatus.READ:
        return None, ReplyOutlineStatus.NONE

    prompt = _build_prompt(processed, raw)
    outline = _call_llm(prompt)

    if processed.is_scheduling_related and processed.calendar_context is not None:
        outline = fold_calendar_slots_into_outline(outline, processed.calendar_context)

    return outline, ReplyOutlineStatus.SUGGESTED


def _build_prompt(processed: ProcessedEmail, raw: RawEmail) -> str:
    return (
        "Draft a short bullet-point outline (not a full email) for replying "
        "to this email. Each bullet should be a brief skeleton point, not "
        "prose - the user will expand it themselves.\n\n"
        f"From: {processed.sender}\n"
        f"Subject: {processed.subject}\n"
        f"Summary: {processed.summary or '(no summary available)'}\n"
        f"Body:\n{raw.body}\n"
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
