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

def _default_model() -> str:
    from llm.client import model_for

    return model_for("classify")
MAX_BODY_LINES = 5
MAX_BODY_CHARS = 500

SYSTEM_PROMPT = (
    "You decide whether an inbox email deserves a human reply. Genuine "
    "personal or business correspondence deserves one; transactional/"
    "automated \"no-reply\" mail (receipts, notifications, newsletters, "
    "marketing) does not — the recipient cannot meaningfully answer it. "
    "You will see only the sender, subject, and the first few lines of the "
    "body. Judge based on tone, content, and whether a human reply would "
    "make sense — not just surface patterns. Anything written by a person "
    "to this recipient — especially a \"Re:\" continuing a real thread — "
    "deserves a reply. Give your reason first, then the answer.\n"
    "\n"
    "Examples:\n"
    '- Sender: no-reply@github.com, Subject: "[repo] Build #412 failed" -> '
    'reason: "automated CI notification; no human reads replies", '
    "deserves_reply: false\n"
    '- Sender: newsletter@theweekly.com, Subject: "This week in AI" -> '
    'reason: "bulk newsletter content", deserves_reply: false\n'
    '- Sender: events@meetup.com, Subject: "Your RSVP is confirmed" -> '
    'reason: "transactional confirmation of an action already taken", '
    "deserves_reply: false\n"
    '- Sender: priya@company.com, Subject: "Re: Q3 planning doc" -> '
    'reason: "a colleague continuing a real thread and asking questions", '
    "deserves_reply: true\n"
    '- Sender: student@university.edu, Subject: "Re: Completed video '
    'homework (late)" -> reason: "a person replying about their own work '
    'and expecting acknowledgement", deserves_reply: true\n'
    '- Sender: alex.chen@gmail.com, Subject: "Quick question about your '
    'talk" -> reason: "an individual asking the recipient something '
    'directly", deserves_reply: true'
)

# Field order is load-bearing: under constrained JSON decoding the model
# emits fields in declaration order, so `reason` MUST be declared before the
# answer — otherwise the answer is committed first and the reasoning merely
# rationalizes it instead of informing it. Do not "tidy" this order.
# maxLength is enforced structurally by constrained decoding, which makes a
# repetition loop inside the string impossible rather than just discouraged.
# Polarity (`deserves_reply`, not `is_no_reply`) is deliberate too: small
# models bias toward answering true, and a false "deserves reply" wastes one
# outline call, while a false "is no-reply" permanently suppresses real
# correspondence.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 200},
        "deserves_reply": {"type": "boolean"},
    },
    "required": ["reason", "deserves_reply"],
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
    """Routed through llm.client so the provider is a config choice.

    See llm/README.md — this stage can run against a local model while
    others stay on a hosted one.
    """
    from llm.client import get_client

    return get_client("classify")


def classify_ambiguous(email: RawEmail, client: Optional[Any] = None) -> tuple[bool, str]:
    """Returns (is_no_reply, reason) via a single Claude API call."""

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(email)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    # The wire schema asks "deserves a reply?" (see RESPONSE_SCHEMA note);
    # the frozen interface returns is_no_reply, so invert here.
    return not bool(data["deserves_reply"]), str(data["reason"])
