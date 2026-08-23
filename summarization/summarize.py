"""Email summarization entry point — matches the frozen signature in
interfaces/README.md.

Produces a factual, detail-carrying summary per email: what it is about,
the specifics (ids, amounts, names, dates), every ask, and any deadline.
Strictly grounded — no inferred action items beyond what's stated/implied.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from models.schema import ContextPack, RawEmail

def _default_model() -> str:
    from llm.client import model_for

    return model_for("summarize")

# Shared with batch.py so single-call and batched summarization follow the
# exact same factual/no-inference rules (DRY — see CLAUDE.md).
# Written for coverage, not brevity. The previous version asked for "1-3
# sentences" and led with "be strictly factual / do not infer", which a small
# model reads as permission to stop early: 80 of 163 summaries came back as a
# single sentence and 59 were under 150 characters, dropping the numbers,
# names and asks that make a summary worth reading. The factual constraint is
# still here — it just no longer sits where it competes with completeness.
SUMMARY_INSTRUCTIONS = (
    "Write a summary that lets the reader act on the email without opening "
    "it. Cover, in this order and only where the email actually contains "
    "them:\n"
    "1. What the email is about, concretely — name the subject matter, not "
    "the genre. \"A shipment is held at customs over an HTS classification "
    "dispute\", not \"an email about logistics\".\n"
    "2. Every specific the reader would otherwise have to open the email to "
    "find: identifiers, order/case/part numbers, quantities, amounts, "
    "percentages, dates, and the names and roles of the people involved.\n"
    "3. Everything being asked of the reader. If there are several asks, "
    "list every one of them — do not collapse them into \"they ask for "
    "some information\".\n"
    "4. Any deadline, and any consequence or decision that is stated.\n"
    "Aim for 3-5 sentences. Use fewer only when the email genuinely contains "
    "less — a one-line notification does not need padding out.\n"
    "\n"
    "Be strictly factual: include only what is explicitly stated or clearly "
    "implied in the email text. Do not invent details, and do not infer "
    "urgency or intentions the email does not express. Completeness means "
    "covering what is there, never adding what is not.\n"
    "\n"
    "The reader is the mailbox owner shown in the To: line — the email was "
    "sent TO them. Never describe the owner as a third party. When the email "
    "asks something of them, including by their name, say it is asked of "
    '"you", and attribute statements and requests to the From: sender. '
    "Write \"Manny Ortiz asks you to confirm the export code\", never "
    "\"the sender asks Ronith to confirm\" or \"the recipient is asked\".\n"
    "\n"
    "Separately, list any dates or deadlines mentioned in the email exactly "
    "as stated (e.g. \"Friday, Aug 28\", \"by EOD Thursday\") — do not "
    "resolve relative dates, guess a year, or convert to another format. "
    "Empty list if none are mentioned."
)

SYSTEM_PROMPT = (
    "You write factual, complete summaries of inbox emails for a busy reader "
    "triaging their inbox. The reader decides what to open next based on your "
    "summary alone, so leaving out a number, a name, or an ask costs them "
    "more than a few extra words does.\n\n" + SUMMARY_INSTRUCTIONS
)

# maxLength is enforced structurally by constrained decoding, which makes a
# repetition loop inside the string impossible rather than just discouraged.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        # Headroom for the 3-5 sentences the instructions ask for. This is a
        # backstop against a runaway loop, not a length target — the
        # instructions set the length. Whatever the value, constrained
        # decoding stops mid-token when it is reached rather than winding the
        # sentence up, so `_trim_to_sentence` below repairs the tail; the two
        # work together and raising one without the other just moves where
        # the mid-word cut lands.
        "summary": {"type": "string", "maxLength": 1600},
        # Bounded the same way summary is — a repetition loop inside the
        # array becomes structurally impossible, not just discouraged.
        "dates": {
            "type": "array",
            "items": {"type": "string", "maxLength": 60},
            "maxItems": 5,
        },
    },
    "required": ["summary", "dates"],
    "additionalProperties": False,
}


# Context budget for summarization, deliberately far below build_pack's 6000
# default. Thread context helps a summary say "the third follow-up on the
# same RMA" instead of restating the thread — but the email being summarized
# is the subject of the call, and on a small model a large context section
# competes with the body for attention rather than adding to it. 1500 chars
# is roughly a thread brief: enough to situate the mail, not enough to
# outweigh it.
SUMMARY_CONTEXT_BUDGET_CHARS = 1500


# Sentence-ending punctuation, including the full-width stops that show up in
# mail quoting Japanese and Chinese correspondents.
_SENTENCE_END = tuple(".!?\u3002\uff01\uff1f")


def _trim_to_sentence(summary: str) -> str:
    """Drop a trailing partial sentence left by the schema's length cap.

    Constrained decoding enforces maxLength by refusing further tokens, which
    stops the model mid-word rather than letting it close the sentence — real
    output ended "...and details about his/a". A summary that stops at its
    last complete sentence is strictly better than one that stops mid-word,
    so this trims back to the last sentence end.

    Only applies when there is a complete sentence to fall back to. A summary
    with no terminator at all — a subject-line-style notification like "Your
    DHL package was delivered" — is returned unchanged rather than emptied.
    """
    summary = summary.strip()
    if not summary or summary.endswith(_SENTENCE_END):
        return summary
    cut = max(summary.rfind(end) for end in _SENTENCE_END)
    return summary[: cut + 1] if cut > 0 else summary


def format_email_for_prompt(email: RawEmail, context: Optional[ContextPack] = None) -> str:
    """Shared email-to-prompt-text formatting, used by both summarize() and
    batch.summarize_batch() so the two stay in sync. `context` defaults to
    None, which preserves the exact prior output — batch.py doesn't pass it
    and its output is unaffected."""
    from llm.prompting import email_identity_block

    header = email_identity_block(email.sender, email.recipients, email.subject)
    parts = [header]
    if context is not None:
        from retrieval.pack import format_context_for_prompt

        context_text = format_context_for_prompt(context)
        if context_text:
            parts.append(context_text)
    parts.append(f"Body:\n{email.body}")
    return "\n".join(parts)


def _get_default_client() -> Any:
    """Routed through llm.client so the provider is a config choice.

    See llm/README.md — this stage can run against a local model while
    others stay on a hosted one.
    """
    from llm.client import get_client

    return get_client("summarize")


def summarize(
    email: RawEmail, client: Optional[Any] = None, context: Optional[ContextPack] = None
) -> Tuple[str, List[str]]:
    """Returns (factual summary, dates mentioned verbatim) via a single
    Claude API call — the dates cost no extra request.

    `context` is an optional retrieval.pack.build_pack() output
    (PHASES-COMPLEX.md B5) — omitting it (the default) preserves this
    function's exact prior behavior.
    """

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        # Must comfortably exceed the schema's maxLength: a response cut off
        # by the token budget is invalid JSON, not a short summary.
        max_tokens=768,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": format_email_for_prompt(email, context)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    summary = _trim_to_sentence(str(data["summary"]))
    dates = [str(d) for d in data["dates"]]

    if not summary:
        # Small models occasionally satisfy the schema with an empty string —
        # 9 of 163 rows in the last full run were blank, and they persisted
        # as "summarized" so no retry ever reached them. Raising instead lets
        # the orchestrator's per-stage error handling record the failure, so
        # the email shows as unsummarized rather than silently summary-less.
        raise ValueError(
            "Summarizer returned an empty summary for {0!r}".format(email.email_id)
        )

    return summary, dates
