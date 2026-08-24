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
# Three bullets, enforced by the schema rather than asked for in prose.
# This prompt has been wrong in both directions: "1-3 sentences" plus a
# lead-in about being strictly factual produced 80 single-sentence summaries
# out of 163, and rewriting it for coverage produced 500-character walls that
# repeated themselves. Neither is what a triage reader wants. Three fixed
# slots force the model to choose what matters instead of padding or stopping
# early, and the array shape makes "three" structural rather than a request a
# small model can drift off.
SUMMARY_INSTRUCTIONS = (
    "Summarize the email as exactly three short bullets, in this order:\n"
    "1. What it is about — the concrete subject matter, with the specific "
    "identifiers, numbers or names that pin it down.\n"
    "2. What is being asked of you — every ask, compressed. Write \"nothing "
    "is asked of you\" if the email requests nothing.\n"
    "3. The deadline or consequence — write \"no deadline stated\" if there "
    "is none.\n"
    "\n"
    "Each bullet is one sentence, at most about 25 words. Be specific rather "
    "than complete: \"CBP is holding shipment RPL-2026-4471 over an HTS code "
    "dispute\" beats a full retelling. Never repeat information across "
    "bullets, and do not write preamble like \"This email is about\".\n"
    "\n"
    "Be strictly factual — only what the email states or clearly implies. Do "
    "not invent details or infer urgency the email does not express.\n"
    "\n"
    "The reader is the mailbox owner in the To: line; the email was sent TO "
    "them. Never describe them in the third person or by name. Write "
    "\"Manny Ortiz asks you to confirm the export code\", never \"the sender "
    "asks Ronith\" or \"the recipient is asked\".\n"
    "\n"
    "Separately, list any dates or deadlines mentioned in the email exactly "
    "as stated (e.g. \"Friday, Aug 28\", \"by EOD Thursday\") — do not "
    "resolve relative dates, guess a year, or convert to another format. "
    "Empty list if none are mentioned."
)

SYSTEM_PROMPT = (
    "You summarize inbox emails for a reader triaging a full inbox at speed. "
    "They read three bullets and decide whether to open the mail. Every word "
    "that is not a fact they would act on is in their way.\n\n"
    + SUMMARY_INSTRUCTIONS
)

# maxLength is enforced structurally by constrained decoding, which makes a
# repetition loop inside a bullet impossible rather than just discouraged.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        # Exactly three, fixed by the schema. min == max is the point: told
        # "three bullets" in prose a small model will happily return one or
        # seven, and constrained decoding is what makes the shape a
        # guarantee. 200 chars fits ~25 words with room for a long
        # identifier; `_trim_to_sentence` repairs the tail if one reaches it.
        "bullets": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "minItems": 3,
            "maxItems": 3,
        },
        # Bounded the same way the bullets are — a repetition loop inside the
        # array becomes structurally impossible, not just discouraged.
        "dates": {
            "type": "array",
            "items": {"type": "string", "maxLength": 60},
            "maxItems": 5,
        },
    },
    "required": ["bullets", "dates"],
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


def join_bullets(bullets: Any) -> str:
    """Render the model's three bullets into the single string the frozen
    `summary` field holds.

    Stored newline-separated with no marker character: consumers that show
    the summary as plain text (the CLI, the agent's tool output, node briefs)
    read it as three lines, and the ones that render it — the extension's
    detail pane — split on the newline and build a real list. Putting a "-"
    in the stored text would show up literally in every plain-text consumer.
    """
    lines = []
    for bullet in bullets or []:
        line = _trim_to_sentence(str(bullet))
        # minItems=3 guarantees three slots, which is the point — but a small
        # model with nothing to put in the third fills it with punctuation
        # ("," was a real response). A slot the model had no content for is
        # dropped rather than rendered as an empty bullet; two real bullets
        # beat three with a comma in the middle.
        if len(line) >= 4 and any(ch.isalnum() for ch in line):
            lines.append(line)
    return "\n".join(lines)


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
    """Returns (three-bullet summary, dates mentioned verbatim) via a single
    Claude API call — the dates cost no extra request.

    `context` is an optional retrieval.pack.build_pack() output
    (PHASES-COMPLEX.md B5) — omitting it (the default) preserves this
    function's exact prior behavior.
    """

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        # Must comfortably exceed the schema's total maxLength: a response
        # cut off by the token budget is invalid JSON, not a short summary.
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": format_email_for_prompt(email, context)}],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    summary = join_bullets(data["bullets"])
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
