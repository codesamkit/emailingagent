"""Persistence for the context graph.

Thin on purpose: this module owns only the row<->object mapping, the same
division `pipeline/persist.py` and `ingestion/store.py` already follow. Every
connection goes through `models.db.connect` / `models.db.prepare`, so path
resolution, WAL mode, and DDL are not re-implemented here.

Two behaviours worth knowing before calling anything:

`upsert_chunks` and `upsert_mentions` REPLACE an email's rows rather than
merging into them. Re-chunking a message with different settings, or
re-extracting it with a better prompt, can legitimately produce FEWER rows than
last time, and a pure upsert keyed on chunk_id would leave the surplus behind
forever — orphan chunks that still answer FTS queries and orphan mentions that
still contribute graph edges. Replacement also keeps `chunk_vec` honest, since
a vector whose chunk no longer exists is deleted with it.

`load_all_vectors` returns ONE contiguous matrix. It is the vector channel's
hot path; a list of arrays there would make every query pay a concatenate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from models import db
from models.schema import (
    Brief,
    BriefNodeType,
    Chunk,
    ChunkKind,
    Entity,
    EntityKind,
    Mention,
    MentionSource,
    Relation,
    RelationKind,
)
from pipeline.incremental import CONTEXT_STAGE_TABLE

from .resolve import EntityIndex

log = logging.getLogger(__name__)


def _prepare(conn: sqlite3.Connection) -> None:
    db.prepare(conn, *db.CONTEXT_SCHEMAS)


def init_db(db_path: Optional[Path] = None) -> None:
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.commit()


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:                          # pragma: no cover - bad row
        return None


def _json_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:                          # pragma: no cover - bad row
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def mention_id_for(mention: Mention) -> str:
    """A content-derived id, so re-extracting an email is an upsert.

    Includes the chunk, the span, and the source: the same entity legitimately
    appears in two chunks of one email, and those are two mentions.
    """
    digest = hashlib.sha1(
        "|".join(
            [
                mention.email_id,
                mention.entity_id,
                mention.chunk_id or "",
                mention.span_text or "",
                MentionSource(mention.source).value,
            ]
        ).encode("utf-8")
    )
    return digest.hexdigest()[:20]


# --- writes ---------------------------------------------------------------

def upsert_chunks(chunks: Sequence[Chunk], *, db_path: Optional[Path] = None) -> int:
    """Replace every chunk of each email present in `chunks`.

    Replacement, not merge: re-chunking can produce fewer rows, and the
    surplus would otherwise stay in the FTS index forever.
    """
    chunks = list(chunks)
    if not chunks:
        return 0
    email_ids = sorted({c.email_id for c in chunks})
    with db.connect(db_path) as conn:
        _prepare(conn)
        placeholders = ",".join("?" for _ in email_ids)
        # Drop every vector belonging to these emails, not just the orphans.
        # Nothing enforces this for us — the schema has no foreign keys — and a
        # chunk_id that survives a re-chunk usually holds DIFFERENT text, so
        # its old vector is stale rather than reusable. Keeping it would be a
        # silent wrong answer in the vector channel. `context_coverage` puts
        # the email back in the embed queue, and embedding is local and free.
        conn.execute(
            "DELETE FROM chunk_vec WHERE chunk_id IN "
            "(SELECT chunk_id FROM chunk WHERE email_id IN ({0}))".format(placeholders),
            email_ids,
        )
        conn.execute(
            "DELETE FROM chunk WHERE email_id IN ({0})".format(placeholders), email_ids
        )
        conn.executemany(
            "INSERT INTO chunk (chunk_id, email_id, ord, text, kind) VALUES (?,?,?,?,?)",
            [
                (c.chunk_id, c.email_id, c.ord, c.text, ChunkKind(c.kind).value)
                for c in chunks
            ],
        )
        conn.commit()
    return len(chunks)


def upsert_vectors(
    pairs: Sequence[Tuple[str, bytes]], *, db_path: Optional[Path] = None
) -> int:
    """(chunk_id, blob) pairs. Blobs must already be normalized — see
    `llm.embeddings.to_blob`, which is the only thing that should build one."""
    pairs = [(cid, blob) for cid, blob in pairs if blob]
    if not pairs:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(
            "INSERT INTO chunk_vec (chunk_id, dim, vec) VALUES (?,?,?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET dim=excluded.dim, vec=excluded.vec",
            [(cid, len(blob) // 4, blob) for cid, blob in pairs],
        )
        conn.commit()
    return len(pairs)


def upsert_entities(entities: Sequence[Entity], *, db_path: Optional[Path] = None) -> int:
    entities = list(entities)
    if not entities:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(
            "INSERT INTO entity (entity_id, kind, canonical_name, normalized_key,"
            " first_seen, last_seen, mention_count, salience)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(entity_id) DO UPDATE SET"
            "   canonical_name = excluded.canonical_name,"
            "   first_seen     = COALESCE(MIN(entity.first_seen, excluded.first_seen),"
            "                            excluded.first_seen, entity.first_seen),"
            "   last_seen      = MAX(COALESCE(entity.last_seen, ''),"
            "                        COALESCE(excluded.last_seen, '')),"
            "   mention_count  = excluded.mention_count,"
            "   salience       = excluded.salience",
            [
                (
                    e.entity_id,
                    EntityKind(e.kind).value,
                    e.canonical_name,
                    e.normalized_key,
                    _dt(e.first_seen),
                    _dt(e.last_seen),
                    e.mention_count,
                    e.salience,
                )
                for e in entities
            ],
        )
        conn.commit()
    # Aliases ride along with the entities that carry them, so a caller never
    # has to remember two calls to persist one resolution result.
    upsert_aliases(
        [(e.entity_id, alias) for e in entities for alias in e.aliases],
        db_path=db_path,
    )
    return len(entities)


def upsert_aliases(
    pairs: Sequence[Tuple[str, str]], *, db_path: Optional[Path] = None
) -> int:
    from .normalize import normalize_name

    rows = []
    with db.connect(db_path) as conn:
        _prepare(conn)
        kinds = {
            row["entity_id"]: EntityKind(row["kind"])
            for row in conn.execute("SELECT entity_id, kind FROM entity")
        }
        for entity_id, alias in pairs:
            kind = kinds.get(entity_id)
            normalized = normalize_name(alias, kind) if alias else ""
            if normalized:
                rows.append((entity_id, alias, normalized))
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO entity_alias (entity_id, alias, normalized_alias)"
                " VALUES (?,?,?)",
                rows,
            )
            conn.commit()
    return len(rows)


def upsert_entity_vectors(
    pairs: Sequence[Tuple[str, bytes]], *, db_path: Optional[Path] = None
) -> int:
    pairs = [(eid, blob) for eid, blob in pairs if blob]
    if not pairs:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(
            "INSERT INTO entity_vec (entity_id, dim, vec) VALUES (?,?,?)"
            " ON CONFLICT(entity_id) DO UPDATE SET dim=excluded.dim, vec=excluded.vec",
            [(eid, len(blob) // 4, blob) for eid, blob in pairs],
        )
        conn.commit()
    return len(pairs)


def upsert_mentions(
    mentions: Sequence[Mention],
    *,
    replace_emails: bool = True,
    db_path: Optional[Path] = None,
) -> int:
    """Replace every mention of each email present in `mentions`.

    `replace_emails=False` is for consolidation, which rewrites entity_ids on
    rows it already loaded and must not delete the ones it is not carrying.
    """
    mentions = list(mentions)
    if not mentions:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        if replace_emails:
            email_ids = sorted({m.email_id for m in mentions})
            conn.execute(
                "DELETE FROM mention WHERE email_id IN ({0})".format(
                    ",".join("?" for _ in email_ids)
                ),
                email_ids,
            )
        conn.executemany(
            "INSERT INTO mention (mention_id, entity_id, email_id, chunk_id,"
            " span_text, confidence, source) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(mention_id) DO UPDATE SET"
            "   entity_id = excluded.entity_id, confidence = excluded.confidence",
            [
                (
                    mention_id_for(m),
                    m.entity_id,
                    m.email_id,
                    m.chunk_id,
                    m.span_text,
                    m.confidence,
                    MentionSource(m.source).value,
                )
                for m in mentions
            ],
        )
        conn.commit()
    return len(mentions)


def replace_mention_entities(
    updates: Sequence[Tuple[str, str]], *, db_path: Optional[Path] = None
) -> int:
    """(mention_id, entity_id) — point already-stored mentions at real entities."""
    updates = list(updates)
    if not updates:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(
            "UPDATE mention SET entity_id = ? WHERE mention_id = ?",
            [(entity_id, mention_id) for mention_id, entity_id in updates],
        )
        conn.commit()
    return len(updates)


def upsert_relations(
    relations: Sequence[Relation], *, db_path: Optional[Path] = None
) -> int:
    relations = list(relations)
    if not relations:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(
            "INSERT INTO relation (src_entity_id, dst_entity_id, rel, weight,"
            " evidence_email_ids) VALUES (?,?,?,?,?)"
            " ON CONFLICT(src_entity_id, dst_entity_id, rel) DO UPDATE SET"
            "   weight = excluded.weight,"
            "   evidence_email_ids = excluded.evidence_email_ids",
            [
                (
                    r.src_entity_id,
                    r.dst_entity_id,
                    RelationKind(r.rel).value,
                    r.weight,
                    json.dumps(sorted(set(r.evidence_email_ids))),
                )
                for r in relations
            ],
        )
        conn.commit()
    return len(relations)


# --- reads ----------------------------------------------------------------

def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        chunk_id=row["chunk_id"],
        email_id=row["email_id"],
        ord=row["ord"],
        text=row["text"],
        kind=ChunkKind(row["kind"]),
    )


def _row_to_entity(row: sqlite3.Row) -> Entity:
    return Entity(
        entity_id=row["entity_id"],
        kind=EntityKind(row["kind"]),
        canonical_name=row["canonical_name"],
        normalized_key=row["normalized_key"],
        first_seen=_parse_dt(row["first_seen"]),
        last_seen=_parse_dt(row["last_seen"]),
        mention_count=row["mention_count"] or 0,
        salience=row["salience"] or 0.0,
    )


def _row_to_mention(row: sqlite3.Row) -> Mention:
    return Mention(
        email_id=row["email_id"],
        entity_id=row["entity_id"],
        span_text=row["span_text"],
        chunk_id=row["chunk_id"],
        confidence=row["confidence"] or 0.0,
        source=MentionSource(row["source"]),
    )


def chunks_for_email(
    email_id: str,
    *,
    kind: Optional[ChunkKind] = None,
    db_path: Optional[Path] = None,
) -> List[Chunk]:
    sql = "SELECT * FROM chunk WHERE email_id = ?"
    params: List[Any] = [email_id]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(ChunkKind(kind).value)
    sql += " ORDER BY ord"
    with db.connect(db_path) as conn:
        _prepare(conn)
        return [_row_to_chunk(row) for row in conn.execute(sql, params)]


def all_entities(
    *,
    kind: Optional[EntityKind] = None,
    db_path: Optional[Path] = None,
) -> List[Entity]:
    sql = "SELECT * FROM entity"
    params: List[Any] = []
    if kind is not None:
        sql += " WHERE kind = ?"
        params.append(EntityKind(kind).value)
    sql += " ORDER BY salience DESC, mention_count DESC"
    with db.connect(db_path) as conn:
        _prepare(conn)
        return [_row_to_entity(row) for row in conn.execute(sql, params)]


def entities_for_email(
    email_id: str, *, db_path: Optional[Path] = None
) -> List[Entity]:
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT e.* FROM entity e JOIN mention m ON m.entity_id = e.entity_id"
            " WHERE m.email_id = ?"
            " GROUP BY e.entity_id ORDER BY e.salience DESC, e.mention_count DESC",
            (email_id,),
        )
        return [_row_to_entity(row) for row in rows]


def emails_for_entity(
    entity_id: str, *, limit: Optional[int] = None, db_path: Optional[Path] = None
) -> List[str]:
    """Email ids mentioning this entity, most recent first.

    Ordered by the email's own timestamp rather than insertion order, because
    every caller of this wants "the latest on X".
    """
    sql = (
        "SELECT m.email_id, MAX(COALESCE(r.received_at, '')) AS at FROM mention m"
        " LEFT JOIN raw_email r ON r.email_id = m.email_id"
        " WHERE m.entity_id = ? GROUP BY m.email_id ORDER BY at DESC"
    )
    params: List[Any] = [entity_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with db.connect(db_path) as conn:
        db.prepare(conn)
        return [row["email_id"] for row in conn.execute(sql, params)]


def mentions_for_email(
    email_id: str, *, db_path: Optional[Path] = None
) -> List[Mention]:
    with db.connect(db_path) as conn:
        _prepare(conn)
        return [
            _row_to_mention(row)
            for row in conn.execute(
                "SELECT * FROM mention WHERE email_id = ? ORDER BY source, span_text",
                (email_id,),
            )
        ]


def all_mentions(
    *, db_path: Optional[Path] = None
) -> List[Tuple[str, Mention]]:
    """(mention_id, Mention) for the whole corpus — consolidation's input."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        return [
            (row["mention_id"], _row_to_mention(row))
            for row in conn.execute("SELECT * FROM mention")
        ]


def relations_for_entity(
    entity_id: str, *, db_path: Optional[Path] = None
) -> List[Relation]:
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT * FROM relation WHERE src_entity_id = ? OR dst_entity_id = ?",
            (entity_id, entity_id),
        )
        return [
            Relation(
                src_entity_id=row["src_entity_id"],
                dst_entity_id=row["dst_entity_id"],
                rel=RelationKind(row["rel"]),
                weight=row["weight"] or 0.0,
                evidence_email_ids=_json_list(row["evidence_email_ids"]),
            )
            for row in rows
        ]


def neighbors(
    entity_id: str,
    *,
    hops: int = 1,
    db_path: Optional[Path] = None,
) -> List[Tuple[Entity, float, int]]:
    """(entity, accumulated edge weight, hop distance), nearest hop first.

    A breadth-first walk over `relation` in both directions — the graph is a
    DAG only in intent, and a walk that followed edges one way would miss half
    the correlation. Weight accumulates multiplicatively so a strong edge two
    hops out can still outrank a weak one at a single hop, which is what makes
    this useful for ranking rather than just reachability.
    """
    with db.connect(db_path) as conn:
        _prepare(conn)
        entities = {
            row["entity_id"]: _row_to_entity(row)
            for row in conn.execute("SELECT * FROM entity")
        }
        edges: Dict[str, List[Tuple[str, float]]] = {}
        for row in conn.execute(
            "SELECT src_entity_id, dst_entity_id, weight FROM relation"
        ):
            weight = float(row["weight"] or 0.0)
            edges.setdefault(row["src_entity_id"], []).append(
                (row["dst_entity_id"], weight)
            )
            edges.setdefault(row["dst_entity_id"], []).append(
                (row["src_entity_id"], weight)
            )

    best: Dict[str, Tuple[float, int]] = {}
    frontier = [(entity_id, 1.0)]
    for hop in range(1, max(1, hops) + 1):
        next_frontier: List[Tuple[str, float]] = []
        for current, carried in frontier:
            for neighbor, weight in edges.get(current, ()):
                if neighbor == entity_id:
                    continue
                score = carried * max(weight, 0.0)
                known = best.get(neighbor)
                if known is None or score > known[0]:
                    best[neighbor] = (score, hop)
                    next_frontier.append((neighbor, score))
        frontier = next_frontier
        if not frontier:
            break

    out = [
        (entities[eid], score, hop)
        for eid, (score, hop) in best.items()
        if eid in entities
    ]
    out.sort(key=lambda item: (item[2], -item[1]))
    return out


def load_entity_index(*, db_path: Optional[Path] = None) -> EntityIndex:
    """Everything resolution needs to match against, in one read."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        index = EntityIndex()
        for row in conn.execute("SELECT * FROM entity"):
            index.add(_row_to_entity(row))
        for row in conn.execute("SELECT * FROM entity_alias"):
            owner = index.by_id.get(row["entity_id"])
            if owner is not None:
                index.by_alias.setdefault(
                    (owner.kind, row["normalized_alias"]), owner.entity_id
                )
        for row in conn.execute("SELECT entity_id, vec FROM entity_vec"):
            index.vectors[row["entity_id"]] = row["vec"]
    return index


def load_all_vectors(
    *, db_path: Optional[Path] = None
) -> Tuple[List[str], "Any"]:
    """(chunk_ids, matrix) — ONE contiguous (n, dim) float32 matrix.

    The vector channel's hot path, so it returns a single array rather than a
    list to concatenate per query. Rows are normalized on write, so similarity
    is `matrix @ query` with no further work. Rows of a minority dimension are
    dropped rather than raising: a corpus embedded with two different models
    should degrade to the majority, not fail every search.
    """
    import numpy

    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT chunk_id, dim, vec FROM chunk_vec ORDER BY chunk_id"
        ).fetchall()

    if not rows:
        return [], numpy.zeros((0, 0), dtype="float32")

    counts: Dict[int, int] = {}
    for row in rows:
        counts[row["dim"]] = counts.get(row["dim"], 0) + 1
    width = max(counts, key=lambda d: counts[d])
    if len(counts) > 1:
        log.warning(
            "chunk_vec holds %d dimensions %s; keeping %d and ignoring the rest",
            len(counts), sorted(counts), width,
        )

    kept = [row for row in rows if row["dim"] == width]
    matrix = numpy.frombuffer(
        b"".join(bytes(row["vec"]) for row in kept), dtype="<f4"
    ).reshape(len(kept), width)
    return [row["chunk_id"] for row in kept], matrix


def context_coverage(*, db_path: Optional[Path] = None) -> Dict[str, Set[str]]:
    """{context stage: email_ids that already have rows for it}.

    Feeds `pipeline.incremental.context_plan`, which stays pure by taking this
    as an argument. The stage->table map lives in `incremental` next to the
    reasoning-pass equivalent, so the two cannot drift.
    """
    queries = {
        "chunk": "SELECT DISTINCT email_id FROM chunk",
        "embed": (
            "SELECT DISTINCT c.email_id FROM chunk c"
            " JOIN chunk_vec v ON v.chunk_id = c.chunk_id"
        ),
        "extract": "SELECT DISTINCT email_id FROM mention",
    }
    assert set(queries) == set(CONTEXT_STAGE_TABLE), "stage map drifted"
    out: Dict[str, Set[str]] = {}
    with db.connect(db_path) as conn:
        _prepare(conn)
        for stage, sql in queries.items():
            out[stage] = {row["email_id"] for row in conn.execute(sql)}
    return out


def counts(*, db_path: Optional[Path] = None) -> Dict[str, int]:
    """Row counts for every context table — the CLI's summary line."""
    tables = (
        "chunk", "chunk_vec", "entity", "entity_alias", "entity_vec",
        "mention", "relation", "node_brief",
    )
    with db.connect(db_path) as conn:
        _prepare(conn)
        return {
            table: conn.execute("SELECT COUNT(*) AS n FROM {0}".format(table)).fetchone()["n"]
            for table in tables
        }


def entity_counts_by_kind(*, db_path: Optional[Path] = None) -> Dict[str, int]:
    with db.connect(db_path) as conn:
        _prepare(conn)
        return {
            row["kind"]: row["n"]
            for row in conn.execute(
                "SELECT kind, COUNT(*) AS n FROM entity GROUP BY kind ORDER BY n DESC"
            )
        }


def email_counts_for_entities(
    *, db_path: Optional[Path] = None
) -> Dict[str, int]:
    """{entity_id: distinct emails mentioning it}, for the whole corpus."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        return {
            row["entity_id"]: row["n"]
            for row in conn.execute(
                "SELECT entity_id, COUNT(DISTINCT email_id) AS n FROM mention"
                " GROUP BY entity_id"
            )
        }


# --- briefs ---------------------------------------------------------------

def _row_to_brief(row: sqlite3.Row) -> Brief:
    return Brief(
        node_type=BriefNodeType(row["node_type"]),
        node_id=row["node_id"],
        headline=row["headline"] or "",
        body_md=row["body_md"] or "",
        open_items=_json_list(row["open_items"]),
        evidence_email_ids=_json_list(row["evidence_email_ids"]),
        evidence_hash=row["evidence_hash"] or "",
        generated_at=_parse_dt(row["generated_at"]),
    )


def get_brief(
    node_type: BriefNodeType,
    node_id: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[Brief]:
    with db.connect(db_path) as conn:
        _prepare(conn)
        row = conn.execute(
            "SELECT * FROM node_brief WHERE node_type = ? AND node_id = ?",
            (BriefNodeType(node_type).value, node_id),
        ).fetchone()
    return _row_to_brief(row) if row else None


def upsert_briefs(briefs: Sequence[Brief], *, db_path: Optional[Path] = None) -> int:
    briefs = list(briefs)
    if not briefs:
        return 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        conn.executemany(
            "INSERT INTO node_brief (node_type, node_id, headline, body_md,"
            " open_items, evidence_email_ids, evidence_hash, generated_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(node_type, node_id) DO UPDATE SET"
            "   headline = excluded.headline, body_md = excluded.body_md,"
            "   open_items = excluded.open_items,"
            "   evidence_email_ids = excluded.evidence_email_ids,"
            "   evidence_hash = excluded.evidence_hash,"
            "   generated_at = excluded.generated_at",
            [
                (
                    BriefNodeType(b.node_type).value,
                    b.node_id,
                    b.headline or None,
                    b.body_md or None,
                    json.dumps(list(b.open_items)),
                    json.dumps(list(b.evidence_email_ids)),
                    b.evidence_hash,
                    _dt(b.generated_at),
                )
                for b in briefs
            ],
        )
        conn.commit()
    return len(briefs)


def mark_briefs_dirty(
    rows: Sequence[Tuple[BriefNodeType, str, Sequence[str], str]],
    *,
    db_path: Optional[Path] = None,
) -> int:
    """Record each node's CURRENT evidence and hash, clearing stale content.

    "Dirty" is `headline IS NULL`. There is no separate flag column, and that
    is deliberate: with one hash column, storing the current hash AND keeping
    the old text would leave nothing to compare against, and a second hash
    column would be a contract change for a bit of state the content itself
    already encodes. Clearing the text when the evidence moves is also the
    honest thing — the old brief is known to be out of date, and showing it as
    if it were current is worse than showing nothing.

    A node whose hash has not changed is left completely untouched, which is
    what makes a no-op run cost zero model calls.
    """
    rows = list(rows)
    if not rows:
        return 0
    dirtied = 0
    with db.connect(db_path) as conn:
        _prepare(conn)
        for node_type, node_id, email_ids, evidence_hash in rows:
            existing = conn.execute(
                "SELECT evidence_hash FROM node_brief WHERE node_type = ? AND node_id = ?",
                (BriefNodeType(node_type).value, node_id),
            ).fetchone()
            if existing and (existing["evidence_hash"] or "") == evidence_hash:
                continue
            conn.execute(
                "INSERT INTO node_brief (node_type, node_id, headline, body_md,"
                " open_items, evidence_email_ids, evidence_hash, generated_at)"
                " VALUES (?, ?, NULL, NULL, '[]', ?, ?, NULL)"
                " ON CONFLICT(node_type, node_id) DO UPDATE SET"
                "   headline = NULL, body_md = NULL, open_items = '[]',"
                "   evidence_email_ids = excluded.evidence_email_ids,"
                "   evidence_hash = excluded.evidence_hash, generated_at = NULL",
                (
                    BriefNodeType(node_type).value,
                    node_id,
                    json.dumps(sorted(email_ids)),
                    evidence_hash,
                ),
            )
            dirtied += 1
        conn.commit()
    return dirtied


def dirty_briefs(*, db_path: Optional[Path] = None) -> List[Brief]:
    """Briefs awaiting generation — `headline IS NULL`. Track B's work queue."""
    with db.connect(db_path) as conn:
        _prepare(conn)
        rows = conn.execute(
            "SELECT * FROM node_brief WHERE headline IS NULL"
            " ORDER BY json_array_length(evidence_email_ids) DESC"
        ).fetchall()
    return [_row_to_brief(row) for row in rows]
