"""Rule-based no-reply detection: sender patterns + bulk/automated headers.

Returns None (inconclusive) when signals conflict or are too weak to be
confident either way — classify.py falls back to the LLM in that case.
"""

from __future__ import annotations

from typing import Optional

from models.schema import RawEmail

# Sender local-part prefixes that are unambiguous by convention — no
# legitimate personal correspondence uses these.
UNAMBIGUOUS_NO_REPLY_PREFIXES = ("no-reply", "noreply", "donotreply", "do-not-reply")

# Sender local-part prefixes commonly used by both fully-automated systems
# AND human-monitored shared inboxes — confident only when corroborated by
# a bulk/automated header.
WEAK_AUTOMATED_PREFIXES = ("notifications", "alerts")

BULK_PRECEDENCE_VALUES = {"bulk", "junk", "list"}


def _local_part(sender: str) -> str:
    """Extract the local-part of an email address, tolerant of a
    "Display Name <addr@domain>" style sender field."""
    addr = sender.strip()
    if "<" in addr and addr.endswith(">"):
        addr = addr[addr.rindex("<") + 1 : -1]
    return addr.split("@", 1)[0].strip().lower()


def _header(headers: dict[str, str], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _addr_only(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if "<" in value and value.endswith(">"):
        value = value[value.rindex("<") + 1 : -1]
    return value.strip().lower()


def apply_rules(email: RawEmail) -> Optional[tuple[bool, str]]:
    """Rule-based pass. Returns (is_no_reply, reason) when confident,
    None when the signals are ambiguous and an LLM call is warranted."""

    sender_local = _local_part(email.sender)
    headers = email.headers or {}

    precedence = (_header(headers, "Precedence") or "").strip().lower()
    list_unsubscribe = _header(headers, "List-Unsubscribe") is not None
    auto_submitted = _header(headers, "Auto-Submitted")
    auto_submitted_set = auto_submitted is not None and auto_submitted.strip().lower() != "no"
    reply_to = _addr_only(_header(headers, "Reply-To"))
    sender_addr = _addr_only(email.sender)
    has_distinct_reply_to = reply_to is not None and reply_to != sender_addr

    has_unambiguous_prefix = sender_local.startswith(UNAMBIGUOUS_NO_REPLY_PREFIXES)
    has_weak_prefix = sender_local.startswith(WEAK_AUTOMATED_PREFIXES)
    has_bulk_precedence = precedence in BULK_PRECEDENCE_VALUES

    if has_unambiguous_prefix:
        return True, f"sender local-part matches no-reply pattern ({sender_local}@...)"

    if has_bulk_precedence:
        return True, f"Precedence header indicates bulk mail (Precedence: {precedence})"

    if list_unsubscribe and not has_distinct_reply_to:
        return True, "List-Unsubscribe present with no distinct reply-to address"

    if has_weak_prefix and (has_bulk_precedence or list_unsubscribe):
        return (
            True,
            f"sender matches automated pattern ({sender_local}@...) corroborated by bulk headers",
        )

    if not has_weak_prefix and not list_unsubscribe and not auto_submitted_set and not precedence:
        return False, "personal sender, no bulk headers"

    return None
