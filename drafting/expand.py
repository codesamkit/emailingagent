"""Expanding an approved outline into full prose.

The expansion itself is implemented: given the outline bullets (the API
passes `email.reply_outline`), a single LLM call on the `outline` stage's
provider turns them into a reply the user can review. Resolving an outline
from an email_id *alone* (the id-only call in interfaces/README.md) is still
deferred — callers that have the outline must pass it.

Human-in-the-loop is unchanged — expanding produces text for the user to
review. Nothing here sends anything.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional


class NotYetImplementedError(NotImplementedError):
    """Raised by the id-only path, which is still a stub. Carries a message
    safe to show a user."""


MAX_BODY_CHARS = 4000  # same bound as outline generation

TONES = ("professional", "concise", "direct", "warm")
DEFAULT_TONE = "professional"

_TONE_INSTRUCTIONS = {
    "professional": "Write in a polished, professional tone.",
    "concise": "Write as briefly as possible — short sentences, no filler, still complete.",
    "direct": "Write plainly and to the point, with minimal pleasantries.",
    "warm": "Write in a warm, friendly tone while staying professional.",
}

SYSTEM_PROMPT = (
    "You write the full text of a reply email from a short outline. Each "
    "outline bullet is one point the reply must make; turn each bullet into "
    "one or two natural sentences, in order, as flowing prose. Write in "
    "first person as the mailbox owner (the To: recipient of the original "
    "email) replying to the From: sender — you ARE the recipient; the sender "
    "is the other person. Never invent "
    "facts, commitments, prices, dates, or availability that are not in the "
    "outline or the original email — if the outline asks to confirm "
    "something you don't know, leave a [placeholder]. No subject line, no "
    "bullet numbers or list markers. Start with a short greeting to the "
    "sender by name and end with a plain sign-off such as \"Best,\"."
)

# maxLength is enforced structurally by constrained decoding, which makes a
# repetition loop inside the string impossible rather than just discouraged.
# minLength matters as much as maxLength here: without a floor, a small
# model under constrained decoding bails after the greeting ("Hello John,").
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "draft": {"type": "string", "minLength": 250, "maxLength": 2500},
    },
    "required": ["draft"],
    "additionalProperties": False,
}


def _default_model() -> str:
    from llm.client import model_for

    return model_for("outline")


def _get_default_client() -> Any:
    """Routed through llm.client so the provider is a config choice.

    See llm/README.md — this stage can run against a local model while
    others stay on a hosted one. Expansion rides the `outline` stage's
    routing: both are the low-volume, judgment-heavy drafting work.
    """
    from llm.client import get_client

    return get_client("outline")


def _build_user_message(email_id: str, outline: List[str]) -> str:
    parts = ["Outline to expand:"]
    parts.extend("- {0}".format(bullet) for bullet in outline)

    # The original email gives the expansion its facts and tone. Missing raw
    # mail degrades to outline-only expansion rather than failing — the
    # outline already encodes what the reply must say.
    from ingestion import store

    try:
        raw = store.get(email_id)
    except Exception:
        raw = None
    if raw is not None:
        from llm.prompting import email_identity_block

        parts.append("")
        parts.append("Original email being replied to:")
        parts.append(email_identity_block(raw.sender, raw.recipients, raw.subject))
        parts.append("Body:\n{0}".format((raw.body or "")[:MAX_BODY_CHARS]))
    return "\n".join(parts)


def expand_outline_to_full_draft(
    email_id: str,
    outline: Optional[List[str]] = None,
    client: Optional[Any] = None,
    tone: str = DEFAULT_TONE,
) -> str:
    """Expand a reply outline into full prose the user can send or edit.

    Requires the outline: the id-only variant (looking the outline up from
    storage by email_id) is a later phase and still raises, so a caller
    without the outline gets an honest "not available yet" rather than
    placeholder prose.

    `tone` picks one of TONES (defaults to "professional" for unknown
    values) and is appended to the system prompt as one extra instruction —
    the facts/structure rules above are unaffected by it.
    """
    if not outline:
        raise NotYetImplementedError(
            "Expanding by email_id alone is not implemented yet "
            "(email_id={0!r}) — the caller must pass the reply outline. "
            "The outline itself is available now.".format(email_id)
        )

    if client is None:
        client = _get_default_client()

    system = SYSTEM_PROMPT + " " + _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS[DEFAULT_TONE])

    response = client.messages.create(
        model=_default_model(),
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": _build_user_message(email_id, outline)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    draft = str(json.loads(text)["draft"]).strip()
    # Constrained decoding on small models occasionally leaks stray JSON
    # punctuation into the start of the string field; drop it.
    return draft.lstrip("}]{[\u0060 \t\n")
