"""Synthetic context-graph fixture DB for developing retrieval/ against.

Person A is building the real context graph (context/chunk.py, context/
extract.py, context/store.py, ...) in parallel — this doesn't wait on them.
Every retrieval/tests/*.py test builds its own DB by calling
`build_fixture_db()`, following pipeline/persist.py's DB-writing idiom
(models.db.connect + models.db.prepare) combined with interface/fixtures.py's
style of hand-built, one-example-per-scenario data.

Centered on the one scenario retrieval has to solve: three emails belonging
to the SAME case, in three DIFFERENT threads, sharing almost no vocabulary —
    - "email-h1a"/"email-h1b" name it by its human label, "Henderson escalation"
    - "email-h2" names it only by ticket ID, "CASE-4471"
    - "email-h3" doesn't name it at all — findable only via the Alex/Jordan
      participant overlap with the other two, i.e. the graph channel alone.

The 8-dim vectors are hand-written (not 768 — small enough to reason about by
hand in a test) along informal "topic axes": dim0=henderson/incident,
dim1=apollo/migration, dim2=participants-only chatter, dim3=lighthouse/
onboarding, dim4=beacon, dim5=atlas/renewal, dim6=pricing/finance, dim7=noise.
h1a/h1b sit high on dim0; h2 sits high on dim0 too (billing text about the
same incident, so it's vector-findable); h3 sits high on dim2 and low on
dim0 — deliberately FAR from h1a/h1b/h2 in vector space, so only the graph
channel (via entity participation, not text) can surface it.
"""

from __future__ import annotations

import json
import struct
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from models import db

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _vec(*components: float) -> bytes:
    """8-dim components -> normalized float32 little-endian blob — the same
    on-disk format llm.embeddings.to_blob will produce (models/db.py's
    chunk_vec/entity_vec column comment), so nothing downstream needs to
    special-case fixture vectors vs. real ones."""
    norm = sum(c * c for c in components) ** 0.5
    normalized = [c / norm for c in components] if norm else list(components)
    return struct.pack("<{0}f".format(len(normalized)), *normalized)


# --- entities ----------------------------------------------------------------
# (entity_id, kind, canonical_name, normalized_key, vec)
_ENTITIES = [
    ("ent-alex", "person", "Alex Rivera", "alex@acme.example", _vec(0, 0, 1, 0, 0, 0, 0, 0)),
    ("ent-jordan", "person", "Jordan Lee", "jordan@acme.example", _vec(0, 0, 1, 0, 0, 0.2, 0, 0)),
    ("ent-dana", "person", "Dana Kim", "dana@vendor.example", _vec(0, 0, 0, 1, 0, 0, 0, 0)),
    ("ent-sam", "person", "Sam Osei", "sam@acme.example", _vec(0, 0, 0, 0, 0, 1, 0, 0)),
    ("ent-taylor", "person", "Taylor Brooks", "taylor@vendor.example", _vec(0, 0, 0, 1, 0.2, 0, 0, 0)),
    ("ent-acme", "org", "Acme Corp", "acme corp", _vec(0.3, 0.3, 0.3, 0, 0, 0.3, 0, 0)),
    ("ent-vendor", "org", "Vendor Co", "vendor co", _vec(0, 0, 0, 0.5, 0.5, 0, 0, 0)),
    ("ent-case-henderson", "case", "Henderson escalation", "henderson escalation", _vec(0.9, 0.3, 0, 0, 0, 0, 0, 0)),
    ("ent-case-lighthouse", "case", "Lighthouse onboarding", "lighthouse onboarding", _vec(0, 0, 0, 0.9, 0.3, 0, 0, 0)),
    ("ent-case-atlas", "case", "Atlas renewal", "atlas renewal", _vec(0, 0.2, 0, 0, 0, 0.9, 0.3, 0)),
    ("ent-proj-apollo", "project", "Apollo platform migration", "apollo platform migration", _vec(0.5, 0.9, 0, 0, 0, 0.2, 0, 0)),
    ("ent-proj-beacon", "project", "Beacon integration", "beacon integration", _vec(0, 0, 0, 0.5, 0.9, 0, 0, 0)),
]

# (entity_id, alias, normalized_alias) — the alias-ladder rung: "CASE-4471"
# resolves to the same entity as "Henderson escalation" without a text match.
_ALIASES = [
    ("ent-case-henderson", "CASE-4471", "case-4471"),
]

# (src, dst, rel, weight, evidence_email_ids)
_RELATIONS = [
    ("ent-case-henderson", "ent-proj-apollo", "belongs_to", 3.0, ["email-h1a", "email-h1b", "email-h2"]),
    ("ent-case-atlas", "ent-proj-apollo", "belongs_to", 2.0, ["email-a1", "email-a2"]),
    ("ent-case-lighthouse", "ent-proj-beacon", "belongs_to", 2.0, ["email-l1", "email-l2"]),
    ("ent-alex", "ent-case-henderson", "participant_in", 3.0, ["email-h1a", "email-h1b", "email-h3"]),
    ("ent-jordan", "ent-case-henderson", "participant_in", 2.0, ["email-h1b", "email-h3"]),
    ("ent-jordan", "ent-case-atlas", "participant_in", 1.0, ["email-a2", "email-a4"]),
    ("ent-dana", "ent-case-lighthouse", "participant_in", 3.0, ["email-l1", "email-l3"]),
    ("ent-taylor", "ent-case-lighthouse", "participant_in", 3.0, ["email-l2", "email-l4"]),
    ("ent-sam", "ent-case-atlas", "participant_in", 3.0, ["email-a1", "email-a3"]),
]


def _email(
    email_id: str,
    thread_id: str,
    sender: str,
    recipients: Sequence[str],
    subject: str,
    days_ago: float,
    chunks: Sequence[tuple],  # (chunk_id_suffix, text, vec)
    mentions: Sequence[tuple] = (),  # (chunk_id_suffix_or_None, entity_id, span_text, confidence, source)
) -> dict:
    return {
        "email_id": email_id,
        "thread_id": thread_id,
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "received_at": NOW - timedelta(days=days_ago),
        "chunks": chunks,
        "mentions": mentions,
    }


_EMAILS = [
    _email(
        "email-h1a", "thread-henderson-name", "alex@acme.example", ["jordan@acme.example"],
        "Henderson escalation - next steps", 3.0,
        chunks=[
            ("1", "The Henderson escalation is still open and the client is asking for a same-day update.", _vec(0.9, 0.3, 0, 0, 0, 0, 0, 0.05)),
            ("2", "I spoke with their ops lead this morning and they want a written summary of the outage timeline.", _vec(0.85, 0.2, 0, 0, 0, 0, 0, 0.1)),
            ("3", "Can you pull the incident log from last Tuesday so I can attach it to the summary?", _vec(0.8, 0.1, 0, 0, 0, 0, 0, 0.05)),
            ("4", "This is blocking the Apollo migration sign-off until it's closed out.", _vec(0.6, 0.8, 0, 0, 0, 0, 0, 0)),
        ],
        mentions=[
            ("1", "ent-case-henderson", "Henderson escalation", 0.95, "llm"),
            ("4", "ent-proj-apollo", "Apollo migration", 0.9, "llm"),
        ],
    ),
    _email(
        "email-h1b", "thread-henderson-name", "jordan@acme.example", ["alex@acme.example"],
        "Re: Henderson escalation - next steps", 2.92,
        chunks=[
            ("1", "Pulled the incident log - attaching it here for the Henderson escalation summary.", _vec(0.9, 0.25, 0, 0, 0, 0, 0, 0.05)),
            ("2", "Ops confirmed the root cause was the failed cutover on the Apollo side, not the client's config.", _vec(0.6, 0.85, 0, 0, 0, 0, 0, 0)),
            ("3", "I'll draft the client-facing update by end of day.", _vec(0.75, 0.1, 0, 0, 0, 0, 0, 0.1)),
        ],
        mentions=[
            ("1", "ent-case-henderson", "Henderson escalation", 0.95, "llm"),
            ("2", "ent-proj-apollo", "Apollo", 0.85, "llm"),
        ],
    ),
    _email(
        "email-h2", "thread-henderson-ticket", "dana@vendor.example", ["alex@acme.example"],
        "Fwd: Invoice adjustment for CASE-4471", 2.0,
        chunks=[
            ("1", "Forwarding the adjusted invoice for CASE-4471 per your request.", _vec(0.75, 0.1, 0, 0, 0, 0, 0.5, 0)),
            ("2", "The credit reflects the downtime hours from last week's incident.", _vec(0.7, 0.05, 0, 0, 0, 0, 0.55, 0)),
            ("3", "Let me know if Acme needs a formal credit memo for their finance team.", _vec(0.4, 0, 0, 0, 0, 0, 0.7, 0)),
            ("4", "Once Acme signs off, we can close this out and move focus back to the Beacon rollout.", _vec(0.3, 0, 0, 0.3, 0.4, 0, 0.3, 0)),
        ],
        mentions=[
            ("1", "ent-case-henderson", "CASE-4471", 1.0, "regex"),
            ("3", "ent-acme", "Acme", 0.9, "llm"),
            ("4", "ent-proj-beacon", "Beacon rollout", 0.8, "llm"),
        ],
    ),
    _email(
        "email-h3", "thread-henderson-participants", "jordan@acme.example", ["alex@acme.example"],
        "quick one", 1.0,
        chunks=[
            ("1", "Did the client come back after your call this morning?", _vec(0.15, 0, 0.9, 0, 0, 0, 0, 0.1)),
            ("2", "If they push again for a discount, loop in Sam before we commit to anything.", _vec(0.1, 0, 0.85, 0, 0, 0.2, 0.2, 0)),
            ("3", "Also - nice work getting that incident log pulled together so fast.", _vec(0.2, 0, 0.9, 0, 0, 0, 0, 0)),
        ],
        mentions=[],  # deliberately no case/project mention — graph-only findable
    ),
    _email(
        "email-l1", "thread-lighthouse", "dana@vendor.example", ["taylor@vendor.example"],
        "Lighthouse onboarding - kickoff notes", 6.0,
        chunks=[
            ("1", "Kickoff went well - Lighthouse wants to go live by the end of next month.", _vec(0, 0, 0, 0.9, 0.2, 0, 0, 0.05)),
            ("2", "They're blocked on SSO configuration before their security team will approve rollout.", _vec(0, 0, 0, 0.85, 0.15, 0, 0, 0)),
            ("3", "I'll send over the Beacon integration checklist so they know what's required on their side.", _vec(0, 0, 0, 0.5, 0.8, 0, 0, 0)),
            ("4", "Taylor, can you own the SSO piece since you handled it for the last onboarding?", _vec(0, 0, 0, 0.8, 0.1, 0, 0, 0.1)),
        ],
        mentions=[
            ("1", "ent-case-lighthouse", "Lighthouse onboarding", 0.95, "llm"),
            ("3", "ent-proj-beacon", "Beacon integration", 0.9, "llm"),
        ],
    ),
    _email(
        "email-l2", "thread-lighthouse", "taylor@vendor.example", ["dana@vendor.example"],
        "Re: Lighthouse onboarding - kickoff notes", 5.9,
        chunks=[
            ("1", "Happy to own SSO - I'll set up a call with their security team this week.", _vec(0, 0, 0, 0.85, 0.1, 0, 0, 0.05)),
            ("2", "The Beacon integration checklist looks accurate, one item is out of date though.", _vec(0, 0, 0, 0.4, 0.85, 0, 0, 0)),
            ("3", "I'll send an updated version by tomorrow.", _vec(0, 0, 0, 0.6, 0.3, 0, 0, 0.1)),
        ],
        mentions=[
            ("2", "ent-proj-beacon", "Beacon integration checklist", 0.85, "llm"),
        ],
    ),
    _email(
        "email-l3", "thread-lighthouse", "dana@vendor.example", ["taylor@vendor.example"],
        "Lighthouse - security call scheduled", 5.0,
        chunks=[
            ("1", "Security call is on the calendar for Thursday at 10am.", _vec(0, 0, 0, 0.8, 0, 0, 0, 0.15)),
            ("2", "Let's have the updated checklist ready before then.", _vec(0, 0, 0, 0.6, 0.4, 0, 0, 0)),
        ],
        mentions=[
            ("1", "ent-case-lighthouse", "Lighthouse", 0.8, "llm"),
        ],
    ),
    _email(
        "email-l4", "thread-lighthouse", "taylor@vendor.example", ["dana@vendor.example"],
        "Re: Lighthouse - security call scheduled", 4.0,
        chunks=[
            ("1", "Updated checklist attached - SSO section is now current.", _vec(0, 0, 0, 0.75, 0.35, 0, 0, 0)),
            ("2", "I think we're in good shape for Thursday.", _vec(0, 0, 0, 0.7, 0, 0, 0, 0.15)),
        ],
        mentions=[],
    ),
    _email(
        "email-a1", "thread-atlas", "sam@acme.example", ["jordan@acme.example"],
        "Atlas renewal - pricing question", 5.0,
        chunks=[
            ("1", "Atlas wants to renew but they're pushing back on the price increase.", _vec(0, 0, 0, 0, 0, 0.9, 0.4, 0)),
            ("2", "Their finance contact asked whether the Apollo migration work could offset part of the cost.", _vec(0.2, 0.3, 0, 0, 0, 0.7, 0.4, 0)),
            ("3", "Can you check if there's flexibility on the Apollo side before I respond?", _vec(0.1, 0.4, 0, 0, 0, 0.6, 0.2, 0)),
            ("4", "Renewal deadline is end of next month, so we shouldn't let this drag.", _vec(0, 0, 0, 0, 0, 0.85, 0.1, 0.1)),
        ],
        mentions=[
            ("1", "ent-case-atlas", "Atlas renewal", 0.95, "llm"),
            ("2", "ent-proj-apollo", "Apollo migration", 0.8, "llm"),
        ],
    ),
    _email(
        "email-a2", "thread-atlas", "jordan@acme.example", ["sam@acme.example"],
        "Re: Atlas renewal - pricing question", 4.96,
        chunks=[
            ("1", "Talked to finance - we can offer a small credit tied to the Apollo migration timeline.", _vec(0.2, 0.3, 0, 0, 0, 0.6, 0.5, 0)),
            ("2", "Send me the renewal number you're proposing and I'll sanity check it.", _vec(0, 0, 0, 0, 0, 0.8, 0.3, 0)),
            ("3", "Also worth mentioning the Henderson incident is unrelated to Atlas, in case they ask.", _vec(0.4, 0, 0, 0, 0, 0.6, 0, 0)),
        ],
        mentions=[
            ("1", "ent-proj-apollo", "Apollo migration", 0.8, "llm"),
            ("3", "ent-case-henderson", "Henderson incident", 0.85, "llm"),
        ],
    ),
    _email(
        "email-a3", "thread-atlas", "sam@acme.example", ["jordan@acme.example"],
        "Atlas - updated proposal", 3.0,
        chunks=[
            ("1", "Sent the updated renewal proposal with the credit included.", _vec(0, 0, 0, 0, 0, 0.85, 0.3, 0)),
            ("2", "They asked for one more week to review internally.", _vec(0, 0, 0, 0, 0, 0.7, 0, 0.15)),
        ],
        mentions=[
            ("1", "ent-case-atlas", "Atlas", 0.7, "llm"),
        ],
    ),
    _email(
        "email-a4", "thread-atlas", "jordan@acme.example", ["sam@acme.example"],
        "Re: Atlas - updated proposal", 2.0,
        chunks=[
            ("1", "Sounds fine - let's follow up next week if we haven't heard back.", _vec(0, 0, 0, 0, 0, 0.7, 0.1, 0.15)),
            ("2", "Flagging this one as high priority given the renewal deadline.", _vec(0, 0, 0, 0, 0, 0.8, 0, 0.1)),
        ],
        mentions=[],
    ),
]

# node_type, node_id, headline, body_md, open_items, evidence_email_ids
_BRIEFS = [
    (
        "thread", "thread-henderson-name",
        "Henderson outage summary in progress",
        "Alex and Jordan are preparing a client-facing summary of the "
        "Henderson outage. Jordan pulled the incident log and confirmed the "
        "root cause was a failed Apollo-side cutover.",
        ["Send client-facing update", "Confirm Apollo sign-off is unblocked"],
        ["email-h1a", "email-h1b"],
    ),
    (
        "case", "ent-case-henderson",
        "Henderson escalation: root cause found, credit issued, sign-off pending",
        "Root cause was a failed Apollo migration cutover, not a client "
        "config issue. Vendor Co issued a billing credit under CASE-4471. "
        "Client has asked about a possible discount beyond the credit; Sam "
        "should be looped in before committing to anything further.",
        [
            "Send client-facing outage summary",
            "Get Acme finance sign-off on the CASE-4471 credit memo",
            "Loop in Sam if the client pushes for an additional discount",
        ],
        ["email-h1a", "email-h1b", "email-h2", "email-h3"],
    ),
    (
        "project", "ent-proj-apollo",
        "Apollo migration: Henderson and Atlas both touch it",
        "The Henderson incident's root cause was an Apollo-side cutover "
        "failure. Separately, Atlas's renewal negotiation is asking whether "
        "Apollo migration work can offset part of their price increase.",
        ["Confirm cutover fix is verified", "Decide Atlas renewal credit tied to Apollo timeline"],
        ["email-h1a", "email-h1b", "email-a1", "email-a2"],
    ),
]


def build_fixture_db() -> Path:
    """Builds the fixture into a fresh SQLite file and returns its path.

    Not a literal ':memory:' path: every retrieval/context function opens its
    own short-lived connection via models.db.connect(path) (this repo's
    connection-per-call convention — see pipeline/persist.py), and SQLite's
    ':memory:' databases are private per-connection, so a literal ':memory:'
    path would look empty to every caller after this function's own
    connection closes. A temp file gives every caller the same data.
    """
    path = Path(tempfile.mkdtemp()) / "retrieval_fixture.db"
    with db.connect(path) as conn:
        db.prepare(conn)
        _populate(conn)
        conn.commit()
    return path


def _populate(conn) -> None:
    now_iso = NOW.isoformat()

    for entity_id, kind, name, key, vec in _ENTITIES:
        conn.execute(
            "INSERT INTO entity (entity_id, kind, canonical_name, normalized_key, "
            "first_seen, last_seen, mention_count, salience) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entity_id, kind, name, key, now_iso, now_iso, 1, 0.5),
        )
        conn.execute(
            "INSERT INTO entity_vec (entity_id, vec) VALUES (?, ?)",
            (entity_id, vec),
        )

    for entity_id, alias, normalized_alias in _ALIASES:
        conn.execute(
            "INSERT INTO entity_alias (entity_id, alias, normalized_alias) VALUES (?, ?, ?)",
            (entity_id, alias, normalized_alias),
        )

    for src, dst, rel, weight, evidence in _RELATIONS:
        conn.execute(
            "INSERT INTO relation (src_entity_id, dst_entity_id, rel, weight, "
            "evidence_email_ids) VALUES (?, ?, ?, ?, ?)",
            (src, dst, rel, weight, json.dumps(evidence)),
        )

    for email in _EMAILS:
        conn.execute(
            "INSERT INTO raw_email (email_id, thread_id, sender, recipients, subject, "
            "body_text, snippet, received_at, read_status, label_ids, headers, "
            "has_attachments, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                email["email_id"],
                email["thread_id"],
                email["sender"],
                json.dumps(list(email["recipients"])),
                email["subject"],
                "\n".join(text for _, text, _ in email["chunks"]),
                email["chunks"][0][1][:100],
                email["received_at"].isoformat(),
                "read",
                json.dumps([]),
                json.dumps({}),
                0,
                now_iso,
            ),
        )

        for suffix, text, vec in email["chunks"]:
            chunk_id = "{0}-c{1}".format(email["email_id"], suffix)
            conn.execute(
                "INSERT INTO chunk (chunk_id, email_id, ord, text, kind) "
                "VALUES (?, ?, ?, ?, 'body')",
                (chunk_id, email["email_id"], int(suffix), text),
            )
            conn.execute(
                "INSERT INTO chunk_vec (chunk_id, dim, vec) VALUES (?, 8, ?)",
                (chunk_id, vec),
            )

        for suffix, entity_id, span_text, confidence, source in email["mentions"]:
            chunk_id = (
                "{0}-c{1}".format(email["email_id"], suffix) if suffix else None
            )
            conn.execute(
                "INSERT INTO mention (entity_id, email_id, chunk_id, span_text, "
                "confidence, source) VALUES (?, ?, ?, ?, ?, ?)",
                (entity_id, email["email_id"], chunk_id, span_text, confidence, source),
            )

    _insert_header_mentions(conn)

    for node_type, node_id, headline, body_md, open_items, evidence in _BRIEFS:
        conn.execute(
            "INSERT INTO node_brief (node_type, node_id, headline, body_md, "
            "open_items, evidence_email_ids, evidence_hash, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node_type,
                node_id,
                headline,
                body_md,
                json.dumps(open_items),
                json.dumps(evidence),
                "fixture-hash-{0}".format(node_id),
                now_iso,
            ),
        )


def _insert_header_mentions(conn) -> None:
    """PERSON mentions from sender/recipients — source=header, chunk_id=None,
    confidence 1.0, matching context/extract.py's documented pass-1 contract
    (interfaces/README.md)."""
    person_by_key = {
        key: entity_id for entity_id, kind, _, key, _ in _ENTITIES if kind == "person"
    }
    for email in _EMAILS:
        for address in [email["sender"], *email["recipients"]]:
            entity_id = person_by_key.get(address)
            if entity_id is None:
                continue
            conn.execute(
                "INSERT INTO mention (entity_id, email_id, chunk_id, span_text, "
                "confidence, source) VALUES (?, ?, NULL, ?, 1.0, 'header')",
                (entity_id, email["email_id"], address),
            )
