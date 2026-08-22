"""Reply-effort estimation, derived from the reply outline.

No LLM call and no schema change: effort is a pure function of fields the
pipeline already produces, computed at read time. The outline is the agent's
own plan for the reply, so its size is the best available proxy for how much
work the reply is. An email with no outline has no effort — "unknown" is
deliberately distinct from "zero" and sorts last.
"""

from __future__ import annotations

from typing import Optional

from models.schema import ProcessedEmail

QUICK = "quick"
MODERATE = "moderate"
INVOLVED = "involved"
LEVELS = (QUICK, MODERATE, INVOLVED)


def reply_effort(email: ProcessedEmail) -> Optional[str]:
    """quick / moderate / involved, or None when there is no outline yet."""
    if not email.reply_outline:
        return None
    points = len(email.reply_outline)
    if email.is_scheduling_related:
        # Coordinating times means back-and-forth beyond the bullets themselves.
        points += 1
    if points <= 2:
        return QUICK
    if points <= 4:
        return MODERATE
    return INVOLVED


def effort_rank(email: ProcessedEmail) -> Optional[int]:
    """Numeric sort key (quick=0 … involved=2); None when effort is unknown."""
    effort = reply_effort(email)
    return LEVELS.index(effort) if effort else None
