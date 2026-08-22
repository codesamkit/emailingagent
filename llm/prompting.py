"""Shared prompt fragments used by every LLM stage that reads an email.

One place for the email-identity header so summarize, outline, and expand
all present sender/recipient the same way. The To: line is what lets a model
tell the mailbox owner apart from the other party — without it, models
routinely describe the owner as a third person or write the reply from the
wrong side of the conversation.
"""

from __future__ import annotations

from typing import Optional, Sequence


def email_identity_block(
    sender: Optional[str],
    recipients: Optional[Sequence[str]],
    subject: Optional[str],
) -> str:
    """From/To/Subject header lines for a prompt.

    Labels are spelled out ("the other party" / "the mailbox owner") right in
    the data block rather than only in system prompts, so the roles survive
    even when a stage's system prompt is trimmed or overridden.
    """
    lines = ["From (the other party, who wrote this email): {0}".format(sender or "unknown")]
    if recipients:
        lines.append(
            "To (the mailbox owner — everything is read and replied to from "
            "their perspective): {0}".format(", ".join(recipients))
        )
    lines.append("Subject: {0}".format(subject or "(no subject)"))
    return "\n".join(lines)
