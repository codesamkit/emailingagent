"""Free-form topic categorization — what an email is *about*.

Assigns each email a short lowercase topic (1-3 words, e.g. "job application",
"order shipping") via a single LLM call. Free-form rather than a fixed enum by
design decision: topics are more descriptive, and fragmentation is contained by
prompting for generic phrasing plus code-side normalization here.

Uses sender + subject + first few lines of body only. Requires an LLM backend
at call time, but not to import this module or run its tests — tests inject a
fake client.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from models.schema import RawEmail

from .llm_fallback import _truncate_body


def _default_model() -> str:
    from llm.client import model_for

    return model_for("categorize")


MAX_TOPIC_WORDS = 3
FALLBACK_TOPIC = "other"

SYSTEM_PROMPT = (
    "You label inbox emails with a topic — what the email is about. Answer "
    "with a single lowercase topic of 1-3 words, generic enough that similar "
    "emails get the same label (the topics group an inbox, so \"order "
    "shipping\", not \"nike order #4821 has shipped\"). Name the subject "
    "matter, not the email's format or importance. Give your reason first, "
    "then the topic.\n"
    "\n"
    "Examples:\n"
    '- Sender: recruiting@company.com, Subject: "Interview availability next '
    'week" -> reason: "hiring process correspondence", topic: "job '
    'application"\n'
    '- Sender: no-reply@amazon.com, Subject: "Your package has shipped" -> '
    'reason: "delivery status for a purchase", topic: "order shipping"\n'
    '- Sender: billing@stripe.com, Subject: "Your invoice for May" -> '
    'reason: "a bill/receipt for a service", topic: "billing"\n'
    '- Sender: priya@company.com, Subject: "Re: Q3 planning doc" -> '
    'reason: "internal discussion of quarterly planning", topic: "team '
    'planning"\n'
    '- Sender: newsletter@theweekly.com, Subject: "This week in AI" -> '
    'reason: "recurring editorial content", topic: "newsletter"\n'
    '- Sender: alerts@bank.com, Subject: "Unusual sign-in detected" -> '
    'reason: "account security notice", topic: "account security"'
)

# Field order is load-bearing: under constrained JSON decoding the model emits
# fields in declaration order, so `reason` MUST be declared before the answer —
# otherwise the topic is committed first and the reasoning merely rationalizes
# it. Do not "tidy" this order. maxLength is enforced structurally by
# constrained decoding, which makes a repetition loop inside the string
# impossible rather than just discouraged.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 150},
        "topic": {"type": "string", "maxLength": 40},
    },
    "required": ["reason", "topic"],
    "additionalProperties": False,
}


def _normalize_topic(raw: str) -> str:
    """Code-level cleanup after the LLM answer (prompt instructions alone
    drift on small models — same posture as scoring's no-reply cap):
    lowercase, collapse whitespace, cap at MAX_TOPIC_WORDS words, and fall
    back to FALLBACK_TOPIC when nothing usable remains."""
    words = (raw or "").lower().strip().strip('."\'').split()
    return " ".join(words[:MAX_TOPIC_WORDS]) or FALLBACK_TOPIC


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

    return get_client("categorize")


def categorize_topic(email: RawEmail, client: Optional[Any] = None) -> str:
    """Returns a normalized free-form topic via a single Claude API call."""

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
    return _normalize_topic(str(data["topic"]))
