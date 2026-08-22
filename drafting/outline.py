"""Reply-outline generation, with a hard code-level eligibility gate.

The gate is the point of this module. `read_status` and `is_no_reply` are
checked with plain `if` statements *before* any LLM client is touched — not
by telling a model to skip ineligible emails. A prompt instruction can drift,
be overridden by a cleverly worded email, or simply be ignored; an `if`
cannot. See CONTEXT.md's design rationale and interfaces/README.md's gating
note, which requires tests to assert the client is never invoked rather than
merely that the output is None.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Tuple

from models.schema import (
    ProcessedEmail,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)

from .calendar_aware import fold_slots_into_outline

log = logging.getLogger(__name__)

def _default_model() -> str:
    from llm.client import model_for

    return model_for("outline")
MAX_BODY_CHARS = 4000
MIN_BULLETS = 2
MAX_BULLETS = 5

SYSTEM_PROMPT = (
    "You draft short reply OUTLINES for an email assistant. An outline is a "
    "list of 2-5 terse bullet points describing what the reply should say — "
    "it is NOT the reply itself. Write bullets as instructions to the sender "
    '("confirm the deadline", "ask which dataset they mean"), not as prose '
    "the user would send verbatim. Be specific to this email: reference the "
    "actual asks, names, and dates in it. Never invent facts, commitments, "
    "prices, or dates that are not in the email. If the email proposes or "
    "asks about meeting times, do NOT guess at availability — the caller "
    "appends real calendar data separately."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": MIN_BULLETS,
            "maxItems": MAX_BULLETS,
        }
    },
    "required": ["bullets"],
    "additionalProperties": False,
}


def is_eligible(processed: ProcessedEmail) -> Tuple[bool, ReplyOutlineStatus]:
    """Whether this email may receive a reply outline, and the status if not.

    Pure and side-effect free so the gate can be tested, reused by the
    pipeline's incremental re-run, and read at a glance:

      - unread          -> never drafted yet; may become eligible when read
      - no-reply        -> never drafted, regardless of read status
      - unclassified    -> not eligible; classification has to run first,
                           otherwise a no-reply email would slip through while
                           `is_no_reply` is still None
    """
    if processed.is_no_reply:
        return False, ReplyOutlineStatus.NOT_APPLICABLE
    if processed.read_status != ReadStatus.READ:
        return False, ReplyOutlineStatus.NONE
    if processed.is_no_reply is None:
        # Unclassified: treat as ineligible rather than assume it is safe.
        return False, ReplyOutlineStatus.NONE
    return True, ReplyOutlineStatus.SUGGESTED


def _build_user_message(processed: ProcessedEmail, raw: RawEmail) -> str:
    parts = [
        "Sender: {0}".format(raw.sender),
        "Subject: {0}".format(raw.subject),
    ]
    if processed.summary:
        parts.append("Summary: {0}".format(processed.summary))
    if processed.is_scheduling_related:
        parts.append(
            "This email is scheduling-related. Do not propose specific times; "
            "the caller appends real availability."
        )
    parts.append("Body:\n{0}".format((raw.body or "")[:MAX_BODY_CHARS]))
    return "\n".join(parts)


def _get_default_client() -> Any:
    """Routed through llm.client so the provider is a config choice.

    See llm/README.md — this stage can run against a local model while
    others stay on a hosted one.
    """
    from llm.client import get_client

    return get_client("outline")


def generate_reply_outline(
    processed: ProcessedEmail,
    raw: RawEmail,
    client: Optional[Any] = None,
    duration_minutes: Optional[int] = None,
    tz=None,
) -> Tuple[Optional[List[str]], ReplyOutlineStatus]:
    """Generate reply-outline bullets for an eligible email.

    Matches the frozen signature in interfaces/README.md:
      - read_status != READ  -> (None, ReplyOutlineStatus.NONE)
      - is_no_reply == True  -> (None, ReplyOutlineStatus.NOT_APPLICABLE)
      - else                 -> (bullets, ReplyOutlineStatus.SUGGESTED)

    The eligibility check happens before `client` is resolved, so an
    ineligible email costs nothing — no API key needed, no network, no tokens.
    """
    eligible, status = is_eligible(processed)
    if not eligible:
        return None, status

    # Only past this line does an LLM client come into existence.
    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(processed, raw)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    bullets = [str(b).strip() for b in json.loads(text)["bullets"] if str(b).strip()]

    bullets = fold_slots_into_outline(bullets, processed, duration_minutes, tz)
    return bullets, ReplyOutlineStatus.SUGGESTED
