"""Stale-outline detection (Phase 8, Track C item 3).

A ProcessedEmail's reply_outline reflects its thread's content as of when
it was generated. If a newer message arrives in the same thread afterward,
that outline is talking about a conversation that has moved on - not
wrong exactly, but misleading to present as the current suggested reply.

There's no persisted "stale" status in the frozen schema
(models/schema.py's ReplyOutlineStatus) - adding one is a shared-contract
change needing all three tracks' sign-off, out of scope for a hardening
pass - so staleness is computed here instead, from data the pipeline
already has: thread_id + received_at.
"""

from __future__ import annotations

from datetime import datetime

from models.schema import ProcessedEmail


def find_stale_outlines(processed_emails: list[ProcessedEmail]) -> list[ProcessedEmail]:
    """Emails whose reply_outline exists but whose thread has since
    received a newer message. The newer message itself is never stale,
    even if it has no outline yet - it just hasn't been processed for one.
    """
    latest_received_by_thread: dict[str, datetime] = {}
    for email in processed_emails:
        current = latest_received_by_thread.get(email.thread_id)
        if current is None or email.received_at > current:
            latest_received_by_thread[email.thread_id] = email.received_at

    return [
        email
        for email in processed_emails
        if email.reply_outline is not None
        and email.received_at < latest_received_by_thread[email.thread_id]
    ]
