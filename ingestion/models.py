"""The `RawEmail` record produced by ingestion.

NOTE: Phase 0 has not run yet, so the shared contract at `models/schema.py`
does not exist. `RawEmail` is defined here field-for-field against the draft
in CONTEXT.md. Once Phase 0 freezes the shared schema, this module collapses
to a re-export:

    from models.schema import RawEmail  # noqa: F401

Nothing outside this package should depend on `RawEmail` living here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# Headers kept verbatim on every message.
#
# The no-reply trio (List-Unsubscribe, Precedence, Auto-Submitted) is required
# by Track B's Phase 2 detection; To/Cc feed Phase 3's direct-vs-CC signal.
# Everything is stored in one JSON blob rather than per-header columns so that
# adding a downstream signal never requires a migration here.
KEPT_HEADERS = (
    "From",
    "To",
    "Cc",
    "Reply-To",
    "Date",
    "Message-ID",
    "List-Unsubscribe",
    "List-Id",
    "Precedence",
    "Auto-Submitted",
    "X-Auto-Response-Suppress",
    "Return-Path",
    "Content-Type",
)

# Headers whose mere presence suggests an automated/bulk sender. Ingestion does
# not classify — Track B owns that — but the CLI surfaces this so a human can
# eyeball that the headers actually survived the round trip.
NO_REPLY_HINT_HEADERS = ("List-Unsubscribe", "Precedence", "Auto-Submitted")

READ = "read"
UNREAD = "unread"


@dataclass(frozen=True)
class RawEmail:
    """One Gmail message, normalized. Immutable: ingestion never mutates."""

    email_id: str
    thread_id: str
    sender: str
    subject: str
    body_text: str
    snippet: str
    received_at: str  # ISO-8601, UTC, e.g. "2026-08-22T14:03:11+00:00"
    read_status: str  # READ | UNREAD
    label_ids: List[str] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    has_attachments: bool = False

    def hint_headers(self) -> Dict[str, str]:
        """The automation-hint headers present on this message, if any."""
        return {
            name: self.headers[name]
            for name in NO_REPLY_HINT_HEADERS
            if name in self.headers
        }
