"""The corpus-wide pass: resolve every mention, derive the edges, dirty the briefs.

This runs ONCE between the context pass and the reasoning pass, and that
position is the whole reason it exists as a separate step. Resolution is a
global question — whether "Henderson escalation" in email 12 and "Henderson
issue" in email 140 are one node cannot be answered while looking at either
email alone — and the reasoning stages retrieve from the answer, so every
email's mentions have to be resolved before any email's outline is generated.

Three things happen here, in order:

  1. Resolve.  Mentions arrive from extraction holding provisional ids
     ("project:atlas"). They are matched against the existing graph by
     `context.resolve`'s deterministic ladder and rewritten to real entity ids.
     Embeddings for the ladder's third rung are computed here, for the name
     kinds only — never for ids or people, which must match exactly.

  2. Relate.  Co-occurrence within an email becomes weighted edges. The one
     rule worth stating: a CASE belongs to a PROJECT only when the two appear
     together in TWO OR MORE emails. One co-occurrence is a coincidence, and a
     graph built on coincidences correlates noise.

  3. Dirty the briefs.  Each thread, case, project and person gets its current
     evidence hashed; a node whose hash moved has its cached brief cleared for
     Track B to regenerate, and a node whose hash is unchanged is not touched
     at all. That skip is where the cost control lives.

Salience is recomputed here too. It is what lets the graph retrieval channel
rank: without it a node mentioned once outranks a project running through
forty emails whenever the walk happens to reach it first.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from models import db
from models.schema import (
    BriefNodeType,
    Entity,
    EntityKind,
    Mention,
    Relation,
    RelationKind,
)

from . import store
from .normalize import parse_provisional
from .resolve import DEFAULT_THRESHOLD, resolve

log = logging.getLogger(__name__)

# A case belongs to a project only on repeated co-occurrence. One shared email
# is a coincidence — a "see also", a digest, a cc'd summary — and an edge built
# on it makes the graph correlate noise with confidence.
MIN_BELONGS_TO_EVIDENCE = 2

# Kinds whose identity is a name, so an embedding comparison is meaningful.
# CASE / DOCUMENT / PERSON are excluded by `context.resolve` regardless; not
# embedding them here as well saves the call.
_EMBEDDABLE_KINDS = frozenset(
    {EntityKind.PROJECT, EntityKind.DELIVERABLE, EntityKind.TOPIC, EntityKind.ORG}
)

# Which kinds get a rollup brief, and under which node type.
_BRIEF_KINDS = {
    EntityKind.CASE: BriefNodeType.CASE,
    EntityKind.PROJECT: BriefNodeType.PROJECT,
    EntityKind.PERSON: BriefNodeType.PERSON,
}


@dataclass
class ConsolidateStats:
    mentions_total: int = 0
    mentions_resolved: int = 0
    entities_created: int = 0
    entities_merged: int = 0
    entities_total: int = 0
    relations_written: int = 0
    briefs_dirtied: int = 0
    by_rung: Dict[str, int] = field(default_factory=dict)

    def as_lines(self) -> List[str]:
        return [
            "mentions      : {0} ({1} newly resolved)".format(
                self.mentions_total, self.mentions_resolved
            ),
            "entities      : {0} total, {1} created, {2} merged by embedding".format(
                self.entities_total, self.entities_created, self.entities_merged
            ),
            "resolve rungs : {0}".format(
                ", ".join(
                    "{0}={1}".format(k, v) for k, v in sorted(self.by_rung.items())
                )
                or "-"
            ),
            "relations     : {0}".format(self.relations_written),
            "briefs dirty  : {0}".format(self.briefs_dirtied),
        ]


def evidence_hash(pairs: Sequence[Tuple[str, Optional[str]]]) -> str:
    """Hash of (email_id, processed_at) pairs — a node's evidence fingerprint.

    Includes processed_at, not just the ids: an email whose summary was
    regenerated is new evidence for the brief above it even though the set of
    emails did not change. Sorted first, so the hash depends on the evidence
    and not on the order a query happened to return it in.
    """
    digest = hashlib.sha1()
    for email_id, processed_at in sorted(pairs):
        digest.update(email_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update((processed_at or "").encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:20]


# --- step 1: resolve ------------------------------------------------------

def _embeddings_for(
    mentions: Sequence[Mention],
    *,
    embed=None,
) -> Dict[str, bytes]:
    """One vector per distinct provisional NAME entity, for resolution rung 3.

    Deduplicated by provisional id: forty mentions of "Atlas" are one text to
    embed, not forty.
    """
    wanted: Dict[str, str] = {}
    for mention in mentions:
        parsed = parse_provisional(mention.entity_id)
        if parsed is None:
            continue
        kind, _ = parsed
        if kind in _EMBEDDABLE_KINDS:
            wanted.setdefault(mention.entity_id, mention.span_text or "")
    if not wanted:
        return {}

    if embed is None:
        from llm.embeddings import embed_texts as embed

    keys = list(wanted)
    try:
        blobs = embed([wanted[key] for key in keys])
    except Exception as exc:
        # Resolution degrades to fully deterministic rather than failing the
        # whole run: rungs 1 and 2 do most of the work anyway, and a missing
        # embedding model must not cost the corpus its graph.
        log.warning("entity embeddings unavailable, rung 3 disabled: %s", exc)
        return {}
    return {key: blob for key, blob in zip(keys, blobs) if blob}


# --- step 2: relations ----------------------------------------------------

# An entity present in more than this share of the corpus carries no
# discriminative information — the mailbox owner, the relay sender, and the
# user's own organization are in nearly every message. Such an entity gets no
# edges at all: untreated it becomes the junction every two-hop walk passes
# through, which turns the graph channel from "related to this" into
# "connected to everything". Standard inverse-document-frequency logic, applied
# to edges instead of terms; edges that survive are still IDF-damped, so a rare
# pair outranks a common one.
STOP_ENTITY_DOC_FRACTION = 0.5

# ...but only once there is enough corpus for "appears in most emails" to mean
# anything. In a five-email mailbox every entity is in more than half of it, and
# applying the rule there produces a graph with no edges at all. Below this
# floor nothing is treated as ubiquitous; the IDF damping still applies, so the
# ranking is right either way, only the pruning waits.
MIN_CORPUS_FOR_STOP_ENTITIES = 20


def _idf(document_frequency: int, total: int) -> float:
    """1.0 for a rare entity, approaching 0 for one in every email."""
    if total <= 0 or document_frequency <= 0:
        return 0.0
    return math.log1p(total / float(document_frequency)) / math.log1p(total)


def _derive_relations(
    per_email: Dict[str, Set[str]],
    kinds: Dict[str, EntityKind],
    confidence: Dict[Tuple[str, str], float],
) -> List[Relation]:
    """Edges from co-occurrence within single emails.

    `per_email` is {email_id: entity_ids}; `confidence` is the best mention
    confidence for an (email, entity) pair, so an id the extraction pass judged
    incidental contributes a weaker edge than the case an email is about.

    Every edge weight is damped by the inverse document frequency of both
    endpoints, and untyped co-occurrence edges touching a corpus-wide entity
    are dropped outright. Without that, the highest-weighted edges in the graph
    are the ones between the mailbox owner, the relay address, and the user's
    own company — true, present in all 163 emails, and worth nothing.
    """
    total = len(per_email)
    frequency: Dict[str, int] = {}
    for entity_ids in per_email.values():
        for entity_id in entity_ids:
            frequency[entity_id] = frequency.get(entity_id, 0) + 1
    idf = {eid: _idf(df, total) for eid, df in frequency.items()}
    ubiquitous = (
        {
            eid
            for eid, df in frequency.items()
            if df / float(total) > STOP_ENTITY_DOC_FRACTION
        }
        if total >= MIN_CORPUS_FOR_STOP_ENTITIES
        else set()
    )

    pair_evidence: Dict[Tuple[str, str, RelationKind], Set[str]] = {}
    pair_weight: Dict[Tuple[str, str, RelationKind], float] = {}

    def record(src: str, dst: str, rel: RelationKind, email_id: str, weight: float) -> None:
        # A corpus-wide entity gets no edges AT ALL, of any type. Damping the
        # weight is not enough: the graph walk still traverses the edge, so the
        # mailbox owner (in 163 of 163 emails) and the relay sender (161) act as
        # a junction connecting every case to every project at two hops, and the
        # graph channel answers "related to everything". Knowing that two cases
        # both involve the mailbox owner conveys nothing — every case does.
        if src in ubiquitous or dst in ubiquitous:
            return
        key = (src, dst, rel)
        pair_evidence.setdefault(key, set()).add(email_id)
        damped = weight * idf.get(src, 1.0) * idf.get(dst, 1.0)
        pair_weight[key] = pair_weight.get(key, 0.0) + damped

    work_kinds = (EntityKind.CASE, EntityKind.PROJECT)
    for email_id, entity_ids in per_email.items():
        ids = sorted(entity_ids)
        people = [i for i in ids if kinds.get(i) == EntityKind.PERSON]
        work = [i for i in ids if kinds.get(i) in work_kinds]
        cases = [i for i in ids if kinds.get(i) == EntityKind.CASE]
        projects = [i for i in ids if kinds.get(i) == EntityKind.PROJECT]

        for person in people:
            for target in work:
                record(
                    person, target, RelationKind.PARTICIPANT_IN, email_id,
                    confidence.get((email_id, target), 1.0),
                )
        for case in cases:
            for project in projects:
                record(
                    case, project, RelationKind.BELONGS_TO, email_id,
                    confidence.get((email_id, case), 1.0),
                )
        # Everything else that co-occurs gets a symmetric, weaker edge. Stored
        # in one canonical direction only; `store.neighbors` walks both ways,
        # so a second row would just double the weight of the same fact.
        for index, src in enumerate(ids):
            for dst in ids[index + 1 :]:
                pair = (src, dst)
                if kinds.get(src) == EntityKind.PERSON and kinds.get(dst) in work_kinds:
                    continue
                if kinds.get(dst) == EntityKind.PERSON and kinds.get(src) in work_kinds:
                    continue
                if {kinds.get(src), kinds.get(dst)} == {EntityKind.CASE, EntityKind.PROJECT}:
                    continue
                record(pair[0], pair[1], RelationKind.MENTIONS, email_id, 0.5)

    relations: List[Relation] = []
    for (src, dst, rel), evidence in pair_evidence.items():
        if rel == RelationKind.BELONGS_TO and len(evidence) < MIN_BELONGS_TO_EVIDENCE:
            continue
        relations.append(
            Relation(
                src_entity_id=src,
                dst_entity_id=dst,
                rel=rel,
                weight=round(pair_weight[(src, dst, rel)], 4),
                evidence_email_ids=sorted(evidence),
            )
        )
    return relations


# --- step 3: salience -----------------------------------------------------

def _salience(entity: Entity, email_count: int, max_emails: int) -> float:
    """How central a node is, 0-1.

    Log-scaled on the email count rather than the mention count: a node named
    forty times in one long thread is less central than one that turns up in
    ten separate conversations, and raw counts let a single verbose email
    dominate. Mention count breaks ties.
    """
    if max_emails <= 0:
        return 0.0
    spread = math.log1p(email_count) / math.log1p(max_emails)
    density = min(1.0, math.log1p(entity.mention_count) / math.log1p(50))
    return round(min(1.0, 0.75 * spread + 0.25 * density), 4)


# --- the pass -------------------------------------------------------------

def consolidate(
    db_path: Optional[Path] = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    embed=None,
) -> ConsolidateStats:
    """Resolve, relate, rank, and dirty the briefs. Idempotent."""
    stats = ConsolidateStats()

    stored = store.all_mentions(db_path=db_path)
    stats.mentions_total = len(stored)
    if not stored:
        return stats

    mentions = [mention for _, mention in stored]
    mention_ids = [mention_id for mention_id, _ in stored]
    pending = [m for m in mentions if parse_provisional(m.entity_id) is not None]

    received = _received_at(db_path)
    provisional_vectors = _embeddings_for(pending, embed=embed) if pending else {}
    result = resolve(
        mentions,
        store.load_entity_index(db_path=db_path),
        embeddings=provisional_vectors,
        received_at=received,
        threshold=threshold,
    )
    stats.mentions_resolved = len(pending)
    stats.entities_created = result.created
    stats.entities_merged = result.merged
    stats.by_rung = dict(result.by_rung)

    store.upsert_entities(result.entities, db_path=db_path)
    store.upsert_aliases(result.aliases, db_path=db_path)
    # Persist the vectors under the entity ids they resolved to. Without this
    # step the embeddings computed above are thrown away and rung 3 can never
    # fire on a later run: every name entity would look brand new forever, and
    # the ladder would silently be two rungs shorter than it reads.
    store.upsert_entity_vectors(
        _entity_vectors(mentions, result.mentions, provisional_vectors),
        db_path=db_path,
    )
    store.replace_mention_entities(
        [
            (mention_id, mention.entity_id)
            for mention_id, mention in zip(mention_ids, result.mentions)
            if mention.entity_id
        ],
        db_path=db_path,
    )

    # Salience, from the now-resolved mention rows.
    entities = store.all_entities(db_path=db_path)
    stats.entities_total = len(entities)
    email_counts = store.email_counts_for_entities(db_path=db_path)
    max_emails = max(email_counts.values()) if email_counts else 0
    store.upsert_entities(
        [
            replace(entity, salience=_salience(
                entity, email_counts.get(entity.entity_id, 0), max_emails
            ))
            for entity in entities
        ],
        db_path=db_path,
    )

    # Relations.
    per_email, kinds, confidence = _cooccurrence(db_path)
    relations = _derive_relations(per_email, kinds, confidence)
    stats.relations_written = store.upsert_relations(relations, db_path=db_path)

    # Briefs.
    stats.briefs_dirtied = store.mark_briefs_dirty(
        _brief_evidence(db_path, kinds), db_path=db_path
    )
    return stats


def _entity_vectors(
    before: Sequence[Mention],
    after: Sequence[Mention],
    provisional_vectors: Dict[str, bytes],
) -> List[Tuple[str, bytes]]:
    """Map each provisional vector onto the entity id its mention resolved to.

    First writer wins per entity, so an entity that already had a vector keeps
    a stable one rather than drifting with whichever surface form was seen last.
    """
    out: Dict[str, bytes] = {}
    for original, resolved in zip(before, after):
        blob = provisional_vectors.get(original.entity_id)
        if blob and resolved.entity_id not in out:
            out[resolved.entity_id] = blob
    return list(out.items())


def _received_at(db_path: Optional[Path]) -> Dict[str, datetime]:
    with db.connect(db_path) as conn:
        db.prepare(conn)
        out: Dict[str, datetime] = {}
        for row in conn.execute("SELECT email_id, received_at FROM raw_email"):
            try:
                out[row["email_id"]] = datetime.fromisoformat(row["received_at"])
            except (TypeError, ValueError):      # pragma: no cover - bad row
                continue
    return out


def _cooccurrence(
    db_path: Optional[Path],
) -> Tuple[Dict[str, Set[str]], Dict[str, EntityKind], Dict[Tuple[str, str], float]]:
    """({email_id: entity_ids}, {entity_id: kind}, {(email, entity): confidence})."""
    per_email: Dict[str, Set[str]] = {}
    confidence: Dict[Tuple[str, str], float] = {}
    with db.connect(db_path) as conn:
        db.prepare(conn, *db.CONTEXT_SCHEMAS)
        kinds = {
            row["entity_id"]: EntityKind(row["kind"])
            for row in conn.execute("SELECT entity_id, kind FROM entity")
        }
        for row in conn.execute(
            "SELECT email_id, entity_id, MAX(confidence) AS c FROM mention"
            " GROUP BY email_id, entity_id"
        ):
            per_email.setdefault(row["email_id"], set()).add(row["entity_id"])
            confidence[(row["email_id"], row["entity_id"])] = float(row["c"] or 1.0)
    return per_email, kinds, confidence


def _brief_evidence(
    db_path: Optional[Path],
    kinds: Dict[str, EntityKind],
) -> List[Tuple[BriefNodeType, str, List[str], str]]:
    """Current evidence and hash for every thread, case, project, and person."""
    with db.connect(db_path) as conn:
        db.prepare(conn, *db.CONTEXT_SCHEMAS)
        processed = {
            row["email_id"]: row["processed_at"]
            for row in conn.execute("SELECT email_id, processed_at FROM processed_email")
        }
        threads: Dict[str, List[str]] = {}
        for row in conn.execute("SELECT email_id, thread_id FROM raw_email"):
            threads.setdefault(row["thread_id"], []).append(row["email_id"])

        entity_emails: Dict[str, List[str]] = {}
        for row in conn.execute(
            "SELECT entity_id, email_id FROM mention GROUP BY entity_id, email_id"
        ):
            entity_emails.setdefault(row["entity_id"], []).append(row["email_id"])

    out: List[Tuple[BriefNodeType, str, List[str], str]] = []

    def add(node_type: BriefNodeType, node_id: str, email_ids: Sequence[str]) -> None:
        email_ids = sorted(set(email_ids))
        out.append(
            (
                node_type,
                node_id,
                list(email_ids),
                evidence_hash([(eid, processed.get(eid)) for eid in email_ids]),
            )
        )

    for thread_id, email_ids in threads.items():
        add(BriefNodeType.THREAD, thread_id, email_ids)
    for entity_id, email_ids in entity_emails.items():
        node_type = _BRIEF_KINDS.get(kinds.get(entity_id))
        if node_type is not None:
            add(node_type, entity_id, email_ids)
    return out
