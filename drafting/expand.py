"""Expanding an approved outline into full prose.

Stub for Phase 5 — the real implementation is a later phase. It is defined
now so Track C's interface and the API can wire the "expand to full draft"
action end-to-end against a predictable failure, rather than an AttributeError
discovered at click time.
"""

from __future__ import annotations

from typing import List, Optional


class NotYetImplementedError(NotImplementedError):
    """Raised by the Phase 5 stub. Carries a message safe to show a user."""


def expand_outline_to_full_draft(
    email_id: str,
    outline: Optional[List[str]] = None,
) -> str:
    """Expand a reply outline into full prose the user can send or edit.

    Deliberately raises rather than returning placeholder text: a caller that
    silently renders "TODO: draft goes here" into a reply box is worse than
    one that shows "not available yet".

    Human-in-the-loop is unchanged when this lands — expanding produces text
    for the user to review. Nothing here sends anything.
    """
    raise NotYetImplementedError(
        "Expanding outline to a full draft is not implemented yet "
        "(email_id={0!r}). The outline itself is available now.".format(email_id)
    )
