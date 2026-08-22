"""Rule-based signal computation for importance scoring.

Computes cheap, deterministic signals (VIP sender, direct vs. CC, urgency
keywords, thread recency, unread-aging) and hands them to scoring/score.py
as context for a single LLM call — the signals themselves don't produce a
score, they just describe the email so the LLM can weigh them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from models.schema import ReadStatus, RawEmail

# Account owner — matches CONTEXT.md's locked-in "Account" decision. No
# contacts/VIP system exists yet in this repo; DEFAULT_VIP_SENDERS is an
# empty placeholder a future phase can populate, and compute_signals accepts
# an override so callers/tests don't need to monkeypatch this constant.
ACCOUNT_OWNER = "iamsamkitshah@gmail.com"
DEFAULT_VIP_SENDERS: frozenset[str] = frozenset()

URGENCY_KEYWORDS = (
    "urgent",
    "asap",
    "as soon as possible",
    "action required",
    "immediately",
    "deadline",
    "important",
    "time-sensitive",
    "time sensitive",
)


def _addr_only(value: str) -> str:
    value = value.strip()
    if "<" in value and value.endswith(">"):
        value = value[value.rindex("<") + 1 : -1]
    return value.strip().lower()


def _header(headers: dict[str, str], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _is_direct(headers: dict[str, str]) -> bool:
    """True when the account owner is in To (or To/headers are missing
    entirely, so an ingestion gap doesn't silently downrank everything).
    False only when the owner shows up solely in Cc."""
    to_header = _header(headers, "To")
    if to_header is None:
        return True
    to_addrs = {_addr_only(a) for a in to_header.split(",") if a.strip()}
    if ACCOUNT_OWNER in to_addrs:
        return True
    cc_header = _header(headers, "Cc")
    cc_addrs = {_addr_only(a) for a in (cc_header or "").split(",") if a.strip()}
    if ACCOUNT_OWNER in cc_addrs:
        return False
    return True


def _urgency_hits(subject: str, body: str) -> list[str]:
    text = f"{subject}\n{body}".lower()
    return [kw for kw in URGENCY_KEYWORDS if kw in text]


def _as_utc(value: datetime) -> datetime:
    """Treat a naive datetime as UTC rather than refusing to compare it.

    `models/schema.py` does not state whether its datetimes are tz-aware, so
    both shapes can reach this function depending on the producer. Assuming
    UTC for naive input matches every producer in the repo and keeps scoring
    from crashing on a fixture built by hand.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def compute_signals(
    email: RawEmail,
    is_no_reply: bool,
    vip_senders: frozenset[str] = DEFAULT_VIP_SENDERS,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Returns a plain dict of rule-based signals for score.py to pass to
    the LLM as context."""

    # Timezone-aware throughout. Ingestion derives received_at from Gmail's
    # internalDate, which is an absolute UTC instant, so an aware value is what
    # actually arrives here; a naive utcnow() made this subtraction raise
    # TypeError the first time real ingested mail was scored.
    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    headers = email.headers or {}
    sender_addr = _addr_only(email.sender)

    hours_since_received = (now - _as_utc(email.received_at)).total_seconds() / 3600.0
    is_unread = email.read_status == ReadStatus.UNREAD

    return {
        "is_vip": sender_addr in vip_senders,
        "is_direct": _is_direct(headers),
        "urgency_keyword_hits": _urgency_hits(email.subject, email.body),
        "hours_since_received": round(hours_since_received, 1),
        "is_unread": is_unread,
        "unread_age_hours": round(hours_since_received, 1) if is_unread else None,
        "is_no_reply": is_no_reply,
    }


def format_signals_for_prompt(signals: dict[str, Any]) -> str:
    lines = [
        f"VIP sender: {signals['is_vip']}",
        f"Addressed directly (To, not just Cc): {signals['is_direct']}",
        "Urgency keywords found: "
        + (", ".join(signals["urgency_keyword_hits"]) if signals["urgency_keyword_hits"] else "none"),
        f"Hours since received: {signals['hours_since_received']}",
        f"Unread: {signals['is_unread']}"
        + (
            f" (unread for {signals['unread_age_hours']} hours)"
            if signals["unread_age_hours"] is not None
            else ""
        ),
        f"Classified as no-reply/automated: {signals['is_no_reply']}",
    ]
    return "\n".join(lines)
