"""Action-item extraction entry point.

Produces the concrete tasks/asks/deadlines an email states or clearly
implies for the recipient — distinct from `summarize.py`'s factual summary,
which explicitly avoids inferring action items, and from
`drafting/outline.py`'s reply outline, which only exists for read,
non-no-reply emails. This stage runs for every email regardless of read or
no-reply status, so a no-reply notification with a real due date (e.g. an
invoice) still surfaces an action item even though it never gets an outline.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from models.schema import RawEmail


def _default_model() -> str:
    from llm.client import model_for

    return model_for("action_items")


ACTION_ITEM_INSTRUCTIONS = (
    "List the concrete action items in this email: things the recipient "
    "needs to do, provide, or decide, including any stated deadline. Base "
    "each item strictly on what the email explicitly asks or requires of "
    'the recipient (the mailbox owner, addressed as "you") — do not invent '
    "tasks that aren't there and do not list things the sender is doing "
    "themselves. Phrase each item as a short actionable phrase (e.g. "
    '"Send the signed contract by Friday"), not a restatement of the whole '
    "email. Empty list if the email asks nothing of the recipient."
)

# An action item is at minimum a short verb phrase ("Pay the invoice"); below
# this it is noise rather than a terse task.
MIN_ITEM_CHARS = 6

SYSTEM_PROMPT = (
    "You extract action items from inbox emails for a busy reader's "
    "to-do list.\n\n" + ACTION_ITEM_INSTRUCTIONS
)

# maxLength/maxItems bound the array the same way summarize.py bounds its
# fields — structurally ruling out a repetition loop inside the JSON.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action_items": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 8,
        },
    },
    "required": ["action_items"],
    "additionalProperties": False,
}


def _get_default_client() -> Any:
    """Routed through llm.client so the provider is a config choice — same
    pattern as every other stage (see llm/README.md)."""
    from llm.client import get_client

    return get_client("action_items")


def extract_action_items(email: RawEmail, client: Optional[Any] = None) -> List[str]:
    """Returns the action items stated/implied in `email` via one LLM call."""

    if client is None:
        client = _get_default_client()

    from summarization.summarize import format_email_for_prompt

    response = client.messages.create(
        model=_default_model(),
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": format_email_for_prompt(email)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    return [
        item.strip()
        for item in (str(raw_item) for raw_item in data["action_items"])
        if is_meaningful(item)
    ]


def is_meaningful(item: str) -> bool:
    """Whether an extracted string is actually an action item.

    The schema bounds length and count but can't require *content*, and the
    model does emit degenerate output -- a bare "," (ten times in the demo
    mailbox) and once a raw JSON fragment, "']}  deficiency:'". Those reach
    the to-do list as unreadable rows, so they are rejected here and filtered
    again in `pipeline/todo.py` for items already persisted.

    Deliberately a shape check, not a quality judgement: anything that reads
    like a phrase is kept, because deciding an item isn't *important* enough
    is the reader's call, not this function's.
    """
    text = (item or "").strip()
    if len(text) < MIN_ITEM_CHARS:
        return False
    # Leading punctuation is the tell for a fragment of the model's own JSON
    # leaking through ("]}  deficiency:"); a real item opens with a word.
    if not text[0].isalnum():
        return False
    # ...and contains at least one actual word, not just digits and symbols.
    return bool(re.search(r"[A-Za-z]{2}", text))
