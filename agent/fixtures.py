"""Hand-written context-graph fixtures standing in for the real retrieval/
context layer (Track A's `context/store.py`, Track B's `retrieval/pack.py`
and `retrieval/briefs.py`) until those packages exist — PHASES-COMPLEX.md's
C1 explicitly asks for this seam so agent/tools.py's retrieval-backed tools
are testable today, swappable to the real thing in one line each.

Mirrors interface/fixtures.py's shape: one deterministic function per
scenario, a fixed clock, no model calls, no DB. Everything here describes
one small synthetic case (a support escalation) that spans a case entity,
a project entity, and two people, so the fixture exercises cross-entity
lookups the same way the real graph eventually will.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from models.schema import Brief, ContextPack, ContextSection, Entity, EntityKind

_NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)

_ENTITIES = [
    Entity(
        entity_id="ent-priya",
        kind=EntityKind.PERSON,
        canonical_name="Priya Shah",
        normalized_key="priya@example.com",
        mention_count=6,
        salience=0.8,
        first_seen=_NOW,
        last_seen=_NOW,
    ),
    Entity(
        entity_id="ent-henderson",
        kind=EntityKind.CASE,
        canonical_name="Henderson escalation",
        normalized_key="hend-4471",
        aliases=["HEND-4471", "Henderson issue"],
        mention_count=4,
        salience=0.65,
        first_seen=_NOW,
        last_seen=_NOW,
    ),
    Entity(
        entity_id="ent-atlas",
        kind=EntityKind.PROJECT,
        canonical_name="Atlas migration",
        normalized_key="atlas migration",
        mention_count=9,
        salience=0.9,
        first_seen=_NOW,
        last_seen=_NOW,
    ),
]

_BRIEFS = {
    ("case", "ent-henderson"): Brief(
        node_type="case",
        node_id="ent-henderson",
        headline="Henderson escalation still open",
        body_md=(
            "Priya flagged this on 2026-08-20; support hasn't confirmed a "
            "root cause yet. No commitment has been made to the customer."
        ),
        open_items=["Confirm root cause with support", "Reply to Priya with an ETA"],
        evidence_email_ids=["demo-related-1"],
        evidence_hash="demo-hash-1",
        generated_at=_NOW,
    ),
    ("thread", "thread-scheduling"): Brief(
        node_type="thread",
        node_id="thread-scheduling",
        headline="Sam wants a quick sync this week",
        body_md="Sam asked for a short sync; no time has been confirmed yet.",
        open_items=["Propose a time"],
        evidence_email_ids=["demo-scheduling"],
        evidence_hash="demo-hash-2",
        generated_at=_NOW,
    ),
}

_OPEN_ITEMS = [
    {"nodeType": "case", "nodeId": "ent-henderson", "item": "Confirm root cause with support"},
    {"nodeType": "case", "nodeId": "ent-henderson", "item": "Reply to Priya with an ETA"},
    {"nodeType": "thread", "nodeId": "thread-scheduling", "item": "Propose a time"},
]


def demo_context_pack(*, query: Optional[str] = None, anchor_email_id: Optional[str] = None) -> ContextPack:
    """Stand-in for retrieval.pack.build_pack. One anchor section plus one
    cross-thread section with no shared vocabulary with the query — the
    scenario the real graph channel exists to solve."""
    sections = [
        ContextSection(
            label="Anchor email",
            text="Sam wants to find time for a quick sync this week.",
            source_email_ids=[anchor_email_id] if anchor_email_id else [],
            score=1.0,
        ),
        ContextSection(
            label="From priya@example.com, 2026-08-20, re: Henderson escalation",
            text=(
                "Priya flagged that the Henderson case (ticket HEND-4471) is "
                "still open and needs a follow-up before it can close."
            ),
            source_email_ids=["demo-related-1"],
            score=0.71,
        ),
    ]
    return ContextPack(
        query=query,
        anchor_email_id=anchor_email_id,
        sections=sections,
        total_chars=sum(len(s.text) for s in sections),
    )


def demo_entities(*, kind: Optional[EntityKind] = None, query: Optional[str] = None) -> List[Entity]:
    entities = list(_ENTITIES)
    if kind is not None:
        entities = [e for e in entities if e.kind == kind]
    if query:
        needle = query.lower().strip()
        entities = [
            e
            for e in entities
            if needle in e.canonical_name.lower()
            or any(needle in alias.lower() for alias in e.aliases)
        ]
    return entities


def demo_thread_brief(thread_id: str) -> Optional[Brief]:
    return _BRIEFS.get(("thread", thread_id))


def demo_entity_brief(entity_id: str) -> Optional[Brief]:
    for (node_type, node_id), brief in _BRIEFS.items():
        if node_type != "thread" and node_id == entity_id:
            return brief
    return None


def demo_open_items(*, person: Optional[str] = None, case: Optional[str] = None) -> List[dict]:
    items = list(_OPEN_ITEMS)
    if case:
        items = [i for i in items if i["nodeId"] == case]
    if person:
        # No person-linked open items in this fixture (would need the
        # entity/relation join the real graph provides) — kept empty rather
        # than faking a link that doesn't exist.
        items = []
    return items
