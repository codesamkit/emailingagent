"""Importance scoring entry point — matches the frozen signature in
interfaces/README.md.

Computes rule-based signals (scoring/signals.py) and passes them as context
to a single LLM call for the final importance_score + justification;
importance_level is then derived deterministically from the score via
fixed thresholds (per CONTEXT.md — the LLM is not asked for the level).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from models.schema import ImportanceLevel, RawEmail
from scoring.signals import compute_signals, format_signals_for_prompt

DEFAULT_MODEL = "claude-opus-5"

# score >= threshold -> level, checked highest first; else LOW.
LEVEL_THRESHOLDS: tuple[tuple[float, ImportanceLevel], ...] = (
    (75.0, ImportanceLevel.URGENT),
    (50.0, ImportanceLevel.HIGH),
    (25.0, ImportanceLevel.MEDIUM),
)

SYSTEM_PROMPT = (
    "You score inbox emails for importance on a 0-100 scale, where 0 is "
    "utterly unimportant (e.g. automated/no-reply mail) and 100 is "
    "critical and time-sensitive. You will see the email's sender, "
    "subject, body, and a set of precomputed rule-based signals (VIP "
    "sender, direct-vs-CC, urgency keywords, recency, unread aging, "
    "no-reply classification). Weigh all of it holistically — a signal "
    "being present doesn't automatically mean high importance, and its "
    "absence doesn't automatically mean low importance. No-reply/automated "
    "mail should almost always score low unless a signal strongly suggests "
    "otherwise."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "importance_score": {"type": "number"},
        "justification": {"type": "string"},
    },
    "required": ["importance_score", "justification"],
    "additionalProperties": False,
}


def _level_from_score(score: float) -> ImportanceLevel:
    for threshold, level in LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return ImportanceLevel.LOW


def _build_user_message(email: RawEmail, signals: dict[str, Any]) -> str:
    return (
        f"Sender: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Body:\n{email.body}\n\n"
        f"Signals:\n{format_signals_for_prompt(signals)}"
    )


def _get_default_client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def score_importance(
    email: RawEmail,
    is_no_reply: bool,
    client: Optional[Any] = None,
) -> tuple[float, ImportanceLevel, str]:
    """Returns (importance_score 0-100, importance_level, justification)."""

    signals = compute_signals(email, is_no_reply)

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(email, signals)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)

    score = max(0.0, min(100.0, float(data["importance_score"])))
    level = _level_from_score(score)
    justification = str(data["justification"])

    return score, level, justification
