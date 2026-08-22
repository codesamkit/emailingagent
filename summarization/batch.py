"""Batched email summarization — matches the frozen signature in
interfaces/README.md.

Packs multiple emails into a single Claude API call (up to BATCH_SIZE per
call) instead of one call per email, for volume efficiency, while chunking
so a single call's prompt size (and failure blast radius) stays bounded.
Reuses summarize.py's SUMMARY_INSTRUCTIONS/format_email_for_prompt so batched
and single-email summaries follow identical rules — same output contract as
calling summarize() on each email individually.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from models.schema import RawEmail
from summarization.summarize import (
    SUMMARY_INSTRUCTIONS,
    _default_model,
    format_email_for_prompt,
)

# Emails per LLM call. Bounds prompt size per call while still amortizing
# per-call overhead across many emails instead of one call per email.
BATCH_SIZE = 20

SYSTEM_PROMPT = (
    "You write short, factual summaries of a batch of inbox emails for a "
    "busy reader triaging their inbox. You will be given multiple emails, "
    "each labeled with its email_id. Summarize each one independently — do "
    "not let one email's content influence another's summary.\n\n" + SUMMARY_INSTRUCTIONS
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["email_id", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summaries"],
    "additionalProperties": False,
}


def _get_default_client() -> Any:
    """Routed through llm.client so the provider is a config choice.

    See llm/README.md — this stage can run against a local model while
    others stay on a hosted one.
    """
    from llm.client import get_client

    return get_client("summarize")


def _build_batch_message(emails: list[RawEmail]) -> str:
    parts = [f"email_id: {email.email_id}\n{format_email_for_prompt(email)}" for email in emails]
    return "\n\n---\n\n".join(parts)


def _chunk(emails: list[RawEmail], size: int) -> list[list[RawEmail]]:
    return [emails[i : i + size] for i in range(0, len(emails), size)]


def summarize_batch(
    emails: list[RawEmail],
    client: Optional[Any] = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, str]:
    """Returns {email_id: summary}. Batches underlying LLM calls (up to
    batch_size emails per call) for volume efficiency; same output contract
    as calling summarize() on each email individually."""

    if not emails:
        return {}

    if client is None:
        client = _get_default_client()

    results: dict[str, str] = {}
    for chunk in _chunk(emails, batch_size):
        response = client.messages.create(
            model=_default_model(),
            max_tokens=256 * len(chunk),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_batch_message(chunk)}],
            output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
        )

        text = next(block.text for block in response.content if block.type == "text")
        data = json.loads(text)
        for item in data["summaries"]:
            results[str(item["email_id"])] = str(item["summary"])

    return results
