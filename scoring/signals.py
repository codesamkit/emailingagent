"""Rule-based signal computation for importance scoring.

Computes cheap, deterministic signals (VIP sender, direct vs. CC, urgency
keywords, thread recency, unread-aging) and hands them to scoring/score.py
as context for a single LLM call — the signals themselves don't produce a
score, they just describe the email so the LLM can weigh them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from models import db
from models.schema import ReadStatus, RawEmail

# No contacts/VIP system exists yet in this repo; DEFAULT_VIP_SENDERS is an
# empty placeholder, and compute_signals accepts an override so
# callers/tests don't need to monkeypatch anything. See compute_vip_senders()
# below for the real (graph-frequency-based) way to populate one, and
# resolve_account_owner() for the real (auth-derived) account owner — both
# are opt-in: compute_signals stays pure/DB-free unless a caller passes them,
# so no existing call site's behavior changes by default (PHASES-COMPLEX.md
# B6 — see the fix note on resolve_account_owner/compute_vip_senders for why
# this is contained rather than wired to run automatically yet).
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


def _is_direct(headers: dict[str, str], account_owner: Optional[str] = None) -> bool:
    """True when the account owner is in To (or To/headers are missing, or
    the owner itself is unresolved, so an ingestion gap or an unresolved
    owner doesn't silently downrank everything). False only when the owner
    shows up solely in Cc."""
    if account_owner is None:
        return True
    to_header = _header(headers, "To")
    if to_header is None:
        return True
    to_addrs = {_addr_only(a) for a in to_header.split(",") if a.strip()}
    if account_owner in to_addrs:
        return True
    cc_header = _header(headers, "Cc")
    cc_addrs = {_addr_only(a) for a in (cc_header or "").split(",") if a.strip()}
    if account_owner in cc_addrs:
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
    account_owner: Optional[str] = None,
) -> dict[str, Any]:
    """Returns a plain dict of rule-based signals for score.py to pass to
    the LLM as context.

    `account_owner` defaults to None (permissive is_direct, same as an
    ingestion gap) rather than a hardcoded address — see
    resolve_account_owner() for the real, opt-in way to supply one.
    """

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
        "is_direct": _is_direct(headers, account_owner),
        "urgency_keyword_hits": _urgency_hits(email.subject, email.body),
        "hours_since_received": round(hours_since_received, 1),
        "is_unread": is_unread,
        "unread_age_hours": round(hours_since_received, 1) if is_unread else None,
        "is_no_reply": is_no_reply,
    }


# --- B6: real (not hardcoded) owner identity and VIP detection -------------
# Both are opt-in — compute_signals only uses them when a caller passes
# them in — so no existing call site changes behavior by default. Wiring
# these to run automatically per pipeline run is a separate, deliberately
# deferred follow-up (see the module docstring above).

_account_owner_cache: dict[str, Optional[str]] = {}


def resolve_account_owner(*, use_cache: bool = True) -> Optional[str]:
    """The authenticated Gmail profile's address (ingestion/gmail_auth.py),
    cached for the process so this never hits the API per email. Returns
    None — not a guess, and not the old hardcoded constant — on ANY
    failure: missing/expired credentials, no network, or a test/CI
    environment with no Gmail auth configured. compute_signals treats None
    exactly like a missing To: header: permissive, not a downrank."""
    if use_cache and "owner" in _account_owner_cache:
        return _account_owner_cache["owner"]
    try:
        from ingestion.gmail_auth import get_gmail_service, get_profile

        service = get_gmail_service(allow_interactive=False)
        owner = get_profile(service).get("emailAddress")
    except Exception:
        owner = None
    if use_cache:
        _account_owner_cache["owner"] = owner
    return owner


def compute_vip_senders(
    db_path=None, *, percentile: float = 90.0
) -> frozenset[str]:
    """PERSON entities (models/db.py's entity table) whose email-exchange
    frequency — distinct emails they're associated with, via the mention
    table — is at or above the given percentile. A frequency proxy, not
    literal two-way (sent+received) volume: this repo ingests inbox mail
    only, with no sent-folder data, so true two-way volume isn't available;
    mention frequency across inbox mail is the closest real signal.

    Returns frozenset() — same as the empty DEFAULT_VIP_SENDERS above — when
    the context graph has no data yet (e.g. Person A's track not merged, or
    a fresh install) or the query fails for any DB-shaped reason, rather
    than raising and taking scoring down with it.
    """
    try:
        with db.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT e.normalized_key AS addr, COUNT(DISTINCT m.email_id) AS n "
                "FROM entity e JOIN mention m ON m.entity_id = e.entity_id "
                "WHERE e.kind = 'person' "
                "GROUP BY e.entity_id"
            ).fetchall()
    except sqlite3.Error:
        return frozenset()

    if not rows:
        return frozenset()

    counts = sorted(row["n"] for row in rows)
    index = min(len(counts) - 1, int(round(percentile / 100.0 * (len(counts) - 1))))
    threshold = counts[index]
    vips = {row["addr"] for row in rows if row["n"] >= threshold}

    # The mailbox owner is the most-mentioned person in their own inbox by
    # construction, so a frequency percentile always elects them. They are
    # never the *sender* of mail arriving here, so the entry can only ever
    # misfire — drop it rather than let it inflate a self-addressed message.
    owner = resolve_account_owner()
    if owner:
        vips.discard(_addr_only(owner))
    return frozenset(vips)


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
