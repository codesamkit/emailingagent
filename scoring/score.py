"""Importance scoring entry point — matches the frozen signature in
interfaces/README.md.

Computes rule-based signals (scoring/signals.py) and passes them as context
to a single LLM call, which picks only the importance_level category (a
4-way choice a model can make reliably) plus a justification. The numeric
importance_score is then computed deterministically: each level owns a fixed
score band, and the rule-based signals place the email within that band, so
ranking within a level is stable and explainable. Asking a model for a
calibrated 0-100 across independently-scored emails demands cross-email
consistency it cannot provide; asking rules for it can.

_level_from_score(score) == level holds by construction, preserving the
documented score->level threshold relationship in CONTEXT.md.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from models.schema import ImportanceLevel, RawEmail
from scoring.signals import compute_signals, format_signals_for_prompt

def _default_model() -> str:
    from llm.client import model_for

    return model_for("score")

# score >= threshold -> level, checked highest first; else LOW. These
# thresholds double as band floors: the LLM-chosen level selects the band,
# and _score_within_band places the score inside it, so score and level can
# never disagree.
LEVEL_THRESHOLDS: tuple[tuple[float, ImportanceLevel], ...] = (
    (75.0, ImportanceLevel.URGENT),
    (50.0, ImportanceLevel.HIGH),
    (25.0, ImportanceLevel.MEDIUM),
)

# (floor, ceiling) of each level's score band, derived from LEVEL_THRESHOLDS.
LEVEL_BANDS: dict[ImportanceLevel, tuple[float, float]] = {
    ImportanceLevel.LOW: (0.0, 25.0),
    ImportanceLevel.MEDIUM: (25.0, 50.0),
    ImportanceLevel.HIGH: (50.0, 75.0),
    ImportanceLevel.URGENT: (75.0, 100.0),
}

SYSTEM_PROMPT = (
    "You categorize inbox emails by importance into exactly one of four "
    "levels: low, medium, high, urgent. You will see the email's sender, "
    "subject, body, and a set of precomputed rule-based signals (VIP "
    "sender, direct-vs-CC, urgency keywords, recency, unread aging, "
    "no-reply classification). Weigh all of it holistically — a signal "
    "being present doesn't automatically mean high importance, and its "
    "absence doesn't automatically mean low importance. Give your "
    "justification first, then the level.\n"
    "\n"
    "Calibration anchors:\n"
    "- low: newsletters, promotions, receipts, automated notifications — "
    "if the sender is a company, mailing list, or automated system and "
    "nothing is asked of the recipient personally, the level is low no "
    "matter how loud, exciting, or time-limited the subject sounds. "
    "Marketing urgency is not recipient urgency. When the signals say "
    "'Classified as no-reply/automated: True', the level is low — the one "
    "exception is a genuine account-security or emergency notification, "
    "which may be medium.\n"
    "- medium: real correspondence with no deadline or concrete ask — an "
    "FYI from a colleague, a routine update, a thread the recipient is "
    "only CC'd on.\n"
    "- high: a real person directly asking the recipient for something — "
    "a question, a review, feedback, guidance, or anything with a stated "
    "deadline. If a person is waiting on the recipient's answer, it is at "
    "least high.\n"
    "- urgent: time-critical and consequential — a same-day deadline, an "
    "emergency, a VIP blocked waiting on the recipient.\n"
    "\n"
    "Examples:\n"
    '- A "50% off this weekend" promo from a retailer -> low ("bulk '
    'marketing, no reply possible").\n'
    '- "Limited time: upgrade your smart home!" from a brand -> low '
    '("promotional; the deadline is the seller\'s, not the recipient\'s").\n'
    '- A colleague\'s "notes from today\'s standup" with no ask -> medium '
    '("relevant but nothing is requested").\n'
    '- "Can you review the contract by Friday?" from a client -> high '
    '("direct ask with a stated deadline").\n'
    '- "Re: My app — could you give me feedback?" from a person -> high '
    '("a person is waiting on the recipient\'s answer").\n'
    '- "Production is down, need you on the call now" from the recipient\'s '
    'manager -> urgent ("immediate, consequential, personally addressed").'
)

# Field order is load-bearing: under constrained JSON decoding the model
# emits fields in declaration order, so `justification` MUST be declared
# before the answer — otherwise the level is committed first and the
# reasoning merely rationalizes it instead of informing it. Do not "tidy"
# this order. maxLength is enforced structurally by constrained decoding,
# which makes a repetition loop inside the string impossible.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "justification": {"type": "string", "maxLength": 300},
        "importance_level": {
            "type": "string",
            "enum": [level.value for level in ImportanceLevel],
        },
    },
    "required": ["justification", "importance_level"],
    "additionalProperties": False,
}


# Cap the body text sent to the model. A multi-thousand-char marketing HTML
# dump drowns a small model (measured: the two biggest bodies in a 12-email
# run were the two misjudged levels) — and the level-relevant information
# lives in the sender, subject, signals, and the opening of the body.
MAX_BODY_CHARS = 2000


def _truncate_body(body: str) -> str:
    if len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS] + "\n[... body truncated ...]"


def _level_from_score(score: float) -> ImportanceLevel:
    for threshold, level in LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return ImportanceLevel.LOW


# Within-band weights for each signal. They sum to 1.0, so the weighted sum
# spans the whole band; the ceiling clamp below keeps a maxed-out email from
# landing exactly on the next band's floor and flipping its level.
_WITHIN_BAND_WEIGHTS = {
    "is_vip": 0.30,
    "is_direct": 0.20,
    "urgency": 0.30,   # up to 3 keyword hits count
    "unread_age": 0.15,  # saturates at 72h unread
    "recency": 0.05,   # fresher mail ranks slightly higher
}


def _score_within_band(level: ImportanceLevel, signals: dict[str, Any]) -> float:
    """Deterministic importance_score inside the band the LLM's level owns.

    The signals only decide *where in the band* the email sits, never which
    band — so two emails at the same level rank stably and explainably, and
    the score can never contradict the level.
    """
    floor, ceiling = LEVEL_BANDS[level]

    if signals["is_no_reply"]:
        # Automated mail gets no within-band boost; pin it to the floor.
        return floor

    weight = 0.0
    if signals["is_vip"]:
        weight += _WITHIN_BAND_WEIGHTS["is_vip"]
    if signals["is_direct"]:
        weight += _WITHIN_BAND_WEIGHTS["is_direct"]
    weight += (
        min(len(signals["urgency_keyword_hits"]), 3) / 3.0
    ) * _WITHIN_BAND_WEIGHTS["urgency"]
    if signals["unread_age_hours"] is not None:
        weight += (
            min(signals["unread_age_hours"], 72.0) / 72.0
        ) * _WITHIN_BAND_WEIGHTS["unread_age"]
    weight += max(
        0.0, 1.0 - min(signals["hours_since_received"], 168.0) / 168.0
    ) * _WITHIN_BAND_WEIGHTS["recency"]

    score = floor + (ceiling - floor) * min(weight, 1.0)
    # Stay strictly inside the band (URGENT's ceiling of 100 is inclusive).
    if level is not ImportanceLevel.URGENT:
        score = min(score, ceiling - 0.1)
    return round(score, 1)


def _build_user_message(email: RawEmail, signals: dict[str, Any]) -> str:
    return (
        f"Sender: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Body:\n{_truncate_body(email.body)}\n\n"
        f"Signals:\n{format_signals_for_prompt(signals)}"
    )


def _get_default_client() -> Any:
    """Routed through llm.client so the provider is a config choice.

    See llm/README.md — this stage can run against a local model while
    others stay on a hosted one.
    """
    from llm.client import get_client

    return get_client("score")


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
        model=_default_model(),
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(email, signals)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)

    # The LLM only picks the category (schema enum guarantees a valid one);
    # the numeric score is placed within that category's band by rules.
    level = ImportanceLevel(data["importance_level"])
    if is_no_reply and level in (ImportanceLevel.URGENT, ImportanceLevel.HIGH):
        # Code-level rule, same philosophy as drafting's outline gate (see
        # CONTEXT.md): automated mail must never outrank human
        # correspondence, however loud it is — prompt instructions alone
        # drift on small models. Medium is the ceiling so a genuine
        # security notice can still surface above the low-band noise.
        level = ImportanceLevel.MEDIUM
    score = _score_within_band(level, signals)
    justification = str(data["justification"])

    return score, level, justification
