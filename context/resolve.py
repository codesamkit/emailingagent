"""Deciding when two mentions are the same thing.

This is the highest-risk module in the layer. Over-merging fuses two real
cases into one node and every downstream answer inherits the confusion;
under-merging shatters one case into eight nodes and the graph stops
correlating anything, which is the entire point of building it. Both failures
are silent — nothing raises, the output just gets worse.

So the ladder is deterministic and ordered, first match wins:

    1. exact normalized_key, within the same EntityKind
    2. a known alias of an existing entity, within the same kind
    3. same-kind embedding cosine >= threshold
    4. otherwise, a new entity

Rungs 1 and 2 are certainties: an exact key or a recorded alias is an identity
claim someone already made. Rung 3 is a guess, and it is the only rung that can
be wrong in the fusing direction, which is why it comes last and why the
threshold is a parameter rather than a constant buried in a call.

CRITICAL DESIGN CONSTRAINT: this module is pure. No database, no model, no
clock. Embeddings are passed IN rather than computed here, so the threshold can
be tuned in a unit test against hand-built low-dimensional vectors whose
similarity you can reason about by eye. That number is a guess; it is the first
thing that moves after real output is seen, and tuning it must not require a
live ollama and a 160-email corpus.

Entity ids are a hash of (kind, normalized_key) rather than random, which makes
the whole pass idempotent: re-resolving the same corpus produces the same ids,
so persistence is an upsert and not a source of duplicates.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from models.schema import Entity, EntityKind, Mention

from .normalize import normalize_id, normalize_name, parse_provisional

# The rung-3 cutoff. High on purpose: "Henderson escalation" and "Henderson
# issue" should merge, "Henderson escalation" and "Meridian escalation" must
# not, and at this corpus size there is not enough text for embeddings to be a
# reliable discriminator, so the deterministic rungs should be doing nearly all
# the work. Treat a large rung-3 count in `ResolveResult.by_rung` as a warning
# sign, not a success.
DEFAULT_THRESHOLD = 0.86

# Kinds whose identity is a machine id. Never merged by embedding: "CS-40350"
# and "CS-40351" are textually and semantically near-identical and are two
# different cases. Only an exact key or a recorded alias may match these.
_EXACT_ONLY_KINDS = frozenset({EntityKind.CASE, EntityKind.DOCUMENT, EntityKind.PERSON})


def entity_id_for(kind: EntityKind, normalized_key: str) -> str:
    """A stable, content-derived id, so re-running is an upsert not a duplicate."""
    digest = hashlib.sha1(
        "{0}:{1}".format(EntityKind(kind).value, normalized_key).encode("utf-8")
    )
    return digest.hexdigest()[:16]


@dataclass
class EntityIndex:
    """Read model over the entities already known. Pure — build it from
    anywhere, including a test literal."""

    by_id: Dict[str, Entity] = field(default_factory=dict)
    # (kind, normalized_key) -> entity_id, and (kind, normalized_alias) -> id
    by_key: Dict[Tuple[EntityKind, str], str] = field(default_factory=dict)
    by_alias: Dict[Tuple[EntityKind, str], str] = field(default_factory=dict)
    # entity_id -> vector blob, for rung 3.
    vectors: Dict[str, bytes] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        entities: Iterable[Entity],
        *,
        vectors: Optional[Mapping[str, bytes]] = None,
    ) -> "EntityIndex":
        index = cls(vectors=dict(vectors or {}))
        for entity in entities:
            index.add(entity)
        return index

    def add(self, entity: Entity) -> None:
        self.by_id[entity.entity_id] = entity
        self.by_key[(entity.kind, entity.normalized_key)] = entity.entity_id
        for alias in entity.aliases:
            normalized = normalize_name(alias, entity.kind)
            if normalized:
                self.by_alias.setdefault((entity.kind, normalized), entity.entity_id)

    def add_alias(self, entity_id: str, alias: str, kind: EntityKind) -> None:
        normalized = normalize_name(alias, kind)
        if normalized:
            self.by_alias.setdefault((kind, normalized), entity_id)


@dataclass
class ResolveResult:
    """What one resolution pass concluded.

    `by_rung` is for tuning, not bookkeeping: it is how you tell "the graph
    looks right" from "the graph looks right because rung 3 merged forty things
    it should not have".
    """

    mentions: List[Mention] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    aliases: List[Tuple[str, str]] = field(default_factory=list)
    created: int = 0
    merged: int = 0
    unresolved: int = 0
    by_rung: Dict[str, int] = field(default_factory=dict)


def _cosine(a: bytes, b: bytes) -> float:
    from llm.embeddings import cosine

    return cosine(a, b)


def _best_vector_match(
    kind: EntityKind,
    vector: bytes,
    index: EntityIndex,
    threshold: float,
) -> Optional[str]:
    """The closest same-kind entity above `threshold`, or None.

    Brute force over the candidates of one kind. At this corpus size that is a
    few hundred comparisons; an index would be machinery for no gain.
    """
    best_id, best_score = None, threshold
    for entity_id, candidate in index.vectors.items():
        entity = index.by_id.get(entity_id)
        if entity is None or entity.kind != kind:
            continue
        if len(candidate) != len(vector):
            continue                       # different embedding model or dim
        score = _cosine(vector, candidate)
        if score >= best_score:
            best_id, best_score = entity_id, score
    return best_id


def resolve(
    mentions: Sequence[Mention],
    existing: Optional[EntityIndex] = None,
    *,
    embeddings: Optional[Mapping[str, bytes]] = None,
    received_at: Optional[Mapping[str, datetime]] = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> ResolveResult:
    """Point every mention at a real entity, creating entities as needed.

    `mentions` carry provisional ids of the form "<kind>:<normalized_key>"
    (see `context.normalize`). `embeddings` maps a provisional id to a vector
    blob and is optional — with none supplied, rung 3 is simply skipped and
    resolution is fully deterministic, which is the mode the unit tests use.
    `received_at` maps email_id to its timestamp and fills first_seen /
    last_seen when available; resolve keeps no clock of its own.
    """
    index = existing or EntityIndex()
    embeddings = embeddings or {}
    received_at = received_at or {}

    result = ResolveResult(by_rung={"exact": 0, "alias": 0, "vector": 0, "new": 0})
    # Entities touched this pass, so the caller upserts each one once with its
    # accumulated mention count rather than once per mention.
    touched: Dict[str, Entity] = {}

    for mention in mentions:
        parsed = parse_provisional(mention.entity_id)
        if parsed is None:
            # Already resolved (a real, opaque id). Pass it through untouched
            # rather than guessing at what it points to.
            result.mentions.append(mention)
            result.unresolved += 1
            continue
        kind, key = parsed
        if not key:
            result.unresolved += 1
            continue

        entity_id, rung = _match(mention, kind, key, index, embeddings, threshold)
        result.by_rung[rung] = result.by_rung.get(rung, 0) + 1
        if rung == "new":
            result.created += 1
        elif rung == "vector":
            result.merged += 1

        entity = touched.get(entity_id) or index.by_id.get(entity_id)
        if entity is None:
            entity = Entity(
                entity_id=entity_id,
                kind=kind,
                canonical_name=mention.span_text or key,
                normalized_key=key,
            )
            index.add(entity)

        entity = _fold_mention(entity, mention, received_at.get(mention.email_id))
        touched[entity_id] = entity
        index.by_id[entity_id] = entity

        # Record the surface form as an alias whenever it is not simply the
        # key. That is what lets rung 2 answer next time for free, instead of
        # rung 3 having to guess again. Id-shaped kinds are skipped: "CS-40350"
        # is already the key modulo separators, and storing it as an alias adds
        # a row that can never match anything the key would not.
        alias = (mention.span_text or "").strip()
        alias_key = normalize_name(alias, kind)
        redundant = (
            not alias
            or not alias_key
            or alias_key == entity.normalized_key
            or normalize_id(alias) == entity.normalized_key
        )
        if not redundant and (kind, alias_key) not in index.by_alias:
            index.add_alias(entity_id, alias, kind)
            result.aliases.append((entity_id, alias))

        result.mentions.append(replace(mention, entity_id=entity_id))

    result.entities = list(touched.values())
    return result


def _match(
    mention: Mention,
    kind: EntityKind,
    key: str,
    index: EntityIndex,
    embeddings: Mapping[str, bytes],
    threshold: float,
) -> Tuple[str, str]:
    """(entity_id, which rung matched). The ladder, in order."""
    # 1. Exact normalized key, within kind. Scoped by kind deliberately: a
    #    PERSON named "Atlas" and a PROJECT named "Atlas" are two things.
    found = index.by_key.get((kind, key))
    if found:
        return found, "exact"

    # 2. A recorded alias — an identity claim already made and stored.
    for candidate in {key, normalize_name(mention.span_text or "", kind)}:
        if candidate:
            found = index.by_alias.get((kind, candidate))
            if found:
                return found, "alias"

    # 3. Embedding similarity. Skipped for id-shaped and address-shaped kinds:
    #    "CS-40350" and "CS-40351" embed almost identically and are different
    #    cases, and two people at one company are not one person.
    vector = embeddings.get(mention.entity_id)
    if vector and kind not in _EXACT_ONLY_KINDS:
        found = _best_vector_match(kind, vector, index, threshold)
        if found:
            return found, "vector"

    # 4. New.
    return entity_id_for(kind, key), "new"


def _fold_mention(
    entity: Entity,
    mention: Mention,
    when: Optional[datetime],
) -> Entity:
    """One more mention folded into an entity: count, timestamps, aliases."""
    aliases = list(entity.aliases)
    alias = (mention.span_text or "").strip()
    if alias and alias != entity.canonical_name and alias not in aliases:
        aliases.append(alias)

    first_seen, last_seen = entity.first_seen, entity.last_seen
    if when is not None:
        first_seen = when if first_seen is None else min(first_seen, when)
        last_seen = when if last_seen is None else max(last_seen, when)

    return replace(
        entity,
        aliases=aliases,
        mention_count=entity.mention_count + 1,
        first_seen=first_seen,
        last_seen=last_seen,
    )
