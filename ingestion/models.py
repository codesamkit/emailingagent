"""The `RawEmail` record produced by ingestion.

This is now the frozen shared contract (`models/schema.py`) plus three
Gmail-specific extras, rather than a parallel definition of the same idea.
Before this change ingestion defined its own incompatible `RawEmail`
(`body_text` instead of `body`, an ISO *string* instead of a `datetime`, no
`recipients`), which meant Track B could not consume anything ingestion
stored — see CONTEXT.md's Checkpoint 1.

Subclassing rather than converting keeps `isinstance(email, schema.RawEmail)`
true, so every Track B and Track C function that types against the contract
accepts an ingestion record directly, with no adapter layer to keep in sync.

The extras are ingestion's own and are deliberately NOT in the shared
contract: `snippet` and `label_ids` are Gmail concepts no other track needs,
and `has_attachments` exists for Phase 8's attachment-only-email handling.
Anything downstream that only knows the contract simply ignores them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from models.schema import RawEmail as SchemaRawEmail
from models.schema import ReadStatus

# Re-exported so the rest of the package (and its tests) has one import site
# for read-status values.
READ = ReadStatus.READ
UNREAD = ReadStatus.UNREAD

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


@dataclass
class RawEmail(SchemaRawEmail):
    """One Gmail message, normalized to the shared contract.

    Not `frozen=True` any more: a dataclass cannot inherit from a non-frozen
    parent and add frozenness, and `models/schema.py` is frozen-by-agreement
    (Phase 0), not by `frozen=True`. Ingestion still never mutates a record
    after construction — that is now a convention here rather than an
    enforced invariant.
    """

    snippet: str = ""
    label_ids: List[str] = field(default_factory=list)
    has_attachments: bool = False

    def hint_headers(self) -> Dict[str, str]:
        """The automation-hint headers present on this message, if any."""
        return {
            name: self.headers[name]
            for name in NO_REPLY_HINT_HEADERS
            if name in self.headers
        }
