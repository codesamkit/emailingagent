"""Email summarization entry point — matches the frozen signature in
interfaces/README.md.

Produces a 1-3 sentence factual summary per email: topic, ask (if any),
deadline (if any). No inferred action items beyond what's stated/implied.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from models.schema import RawEmail

DEFAULT_MODEL = "claude-opus-5"

# Shared with batch.py so single-call and batched summarization follow the
# exact same factual/no-inference rules (DRY — see CLAUDE.md).
SUMMARY_INSTRUCTIONS = (
    "Summarize each email in 1-3 sentences covering: what it's about, what "
    "(if anything) is being asked of the recipient, and any deadline "
    "mentioned. Be strictly factual — base the summary only on what is "
    "explicitly stated or clearly implied in the email text. Do not infer "
    "action items, intentions, or urgency beyond what the email itself says."
)

SYSTEM_PROMPT = (
    "You write short, factual summaries of inbox emails for a busy reader "
    "triaging their inbox.\n\n" + SUMMARY_INSTRUCTIONS
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
    "additionalProperties": False,
}


def format_email_for_prompt(email: RawEmail) -> str:
    """Shared email-to-prompt-text formatting, used by both summarize() and
    batch.summarize_batch() so the two stay in sync."""

    return f"Sender: {email.sender}\nSubject: {email.subject}\nBody:\n{email.body}"


def _get_default_client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def summarize(email: RawEmail, client: Optional[Any] = None) -> str:
    """Returns a 1-3 sentence factual summary via a single Claude API call."""

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": format_email_for_prompt(email)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    return str(data["summary"])
