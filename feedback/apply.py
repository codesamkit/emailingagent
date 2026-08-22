"""Apply stored sender priors to processed emails.

Runs after the LLM stages (pipeline/cli.py calls it before the score-spread
pass), so however the model classified or leveled an email, your recorded
correction for that sender has the last word — the same philosophy as the
outline gate: hard, user-stated rules live in code, not in prompts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from models.schema import ImportanceLevel, ProcessedEmail, ReplyOutlineStatus
from scoring.score import LEVEL_BANDS, _score_within_band
from scoring.signals import _addr_only, compute_signals

from .store import sender_priors


def _rescore(email: ProcessedEmail, level: ImportanceLevel, db_path: Optional[Path]) -> float:
    """Place the pinned level's score within its band using the email's own
    signals when the raw email is still around; band floor otherwise."""
    from ingestion import store

    try:
        raw = store.get(email.email_id, db_path)
    except Exception:
        raw = None
    if raw is None:
        return LEVEL_BANDS[level][0]
    return _score_within_band(level, compute_signals(raw, bool(email.is_no_reply)))


def apply_feedback(
    emails: Iterable[ProcessedEmail],
    priors: Optional[Dict[str, Dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> List[ProcessedEmail]:
    """Overrides is_no_reply / importance_level on every email whose sender
    has a recorded correction. Returns the emails that changed."""
    if priors is None:
        priors = sender_priors(db_path)
    if not priors:
        return []

    changed: List[ProcessedEmail] = []
    for email in emails:
        prior = priors.get(_addr_only(email.sender))
        if not prior:
            continue
        touched = False

        if "is_no_reply" in prior and email.is_no_reply != prior["is_no_reply"]:
            email.is_no_reply = prior["is_no_reply"]
            email.no_reply_reason = (
                "your feedback: sender marked automated"
                if prior["is_no_reply"]
                else "your feedback: sender marked as a real correspondent"
            )
            if prior["is_no_reply"]:
                # The outline gate's rule, enforced here too: no-reply mail
                # never keeps a reply outline.
                email.reply_outline = None
                email.reply_outline_status = ReplyOutlineStatus.NOT_APPLICABLE
            elif email.reply_outline_status == ReplyOutlineStatus.NOT_APPLICABLE:
                # Now eligible in principle; the next pipeline run drafts one.
                email.reply_outline_status = ReplyOutlineStatus.NONE
            touched = True

        wanted_level = prior.get("level")
        if wanted_level is not None and (
            email.importance_level != wanted_level or email.importance_score is None
        ):
            email.importance_level = wanted_level
            email.importance_score = _rescore(email, wanted_level, db_path)
            email.importance_justification = (
                "Pinned to {0} by your feedback on this sender.".format(wanted_level.value)
            )
            touched = True

        if touched:
            changed.append(email)
    return changed
