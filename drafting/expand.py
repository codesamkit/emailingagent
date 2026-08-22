"""expand_outline_to_full_draft - on-demand full-email expansion.

Stub for now (Phase 5 only requires the outline). Real implementation
lands later, once there's a persistence layer to look email_id up
against - see interfaces/README.md.
"""

from __future__ import annotations


def expand_outline_to_full_draft(email_id: str) -> str:
    raise NotImplementedError(
        f"expand_outline_to_full_draft not yet implemented (email_id={email_id!r})"
    )
