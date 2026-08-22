"""LLM fallback for no-reply classification when the rule-based pass
(classification/rules.py) is inconclusive.

Uses sender + subject + first few lines of body only. Requires the
`anthropic` package and an API key (ANTHROPIC_API_KEY or an `ant auth
login` profile) at call time, but neither is needed to import this module
or to run classification/tests/test_classification.py, since tests inject
a fake client.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from models.schema import RawEmail

DEFAULT_MODEL = "claude-opus-5"
MAX_BODY_LINES = 5
MAX_BODY_CHARS = 500

SYSTEM_PROMPT = (
    "You classify inbox emails as either transactional/automated \"no-reply\" "
    "mail (which the recipient cannot meaningfully reply to and should never "
    "receive a drafted reply for) or genuine personal/business correspondence "
    "that deserves a reply. You will see only the sender, subject, and the "
    "first few lines of the body. Judge based on tone, content, and whether "
    "a human reply would make sense — not just surface patterns."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_no_reply": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_no_reply", "reason"],
    "additionalProperties": False,
}


def _truncate_body(body: str) -> str:
    lines = body.splitlines()[:MAX_BODY_LINES]
    truncated = "\n".join(lines)
    return truncated[:MAX_BODY_CHARS]


def _build_user_message(email: RawEmail) -> str:
    return (
        f"Sender: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Body (first lines):\n{_truncate_body(email.body)}"
    )


def _get_default_client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def classify_ambiguous(email: RawEmail, client: Optional[Any] = None) -> tuple[bool, str]:
    """Returns (is_no_reply, reason) via a single Claude API call."""

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(email)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    return bool(data["is_no_reply"]), str(data["reason"])
