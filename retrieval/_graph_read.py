"""Retrieval-owned seam over the context-graph tables (models/db.py),
standing in for Person A's context/store.py (PHASES-COMPLEX.md A5), which
doesn't exist in this repo yet — see interfaces/README.md's context-graph
section for why this exists and how to retire it.

Same function names and return shapes A5 specifies, so swapping the import
in search.py for `from context.store import ...` is mechanical once that
track lands. This module is retrieval/'s own — it does not live in
/context/, so ownership stays clean; nobody outside retrieval/ should import
it directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models import db

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a declared dependency
    np = None


def entities_for_email(email_id: str, *, db_path: Optional[Path] = None) -> List[dict]:
    """Entities mentioned in this email, most-salient first. Plain dicts
    (entity_id, kind, canonical_name, salience) — enough to drive the graph
    channel and brief lookups without needing models.schema.Entity's full
    row mapping, which is context/store.py's job."""
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT e.entity_id, e.kind, e.canonical_name, e.salience "
            "FROM mention m JOIN entity e ON e.entity_id = m.entity_id "
            "WHERE m.email_id = ? ORDER BY e.salience DESC",
            (email_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def neighbors(
    entity_id: str, *, hops: int = 1, db_path: Optional[Path] = None
) -> List[dict]:
    """Entities reachable from entity_id within `hops` relation edges
    (either direction). Each result dict carries `_edge_weight` (the best
    path's weight, decayed per hop) and `_hops` (the hop distance it was
    first reached at), so callers can score/decay without a second query."""
    with db.connect(db_path) as conn:
        best: Dict[str, Tuple[float, int]] = {}
        frontier = {entity_id: 1.0}
        visited = {entity_id}
        for hop in range(1, hops + 1):
            next_frontier: Dict[str, float] = {}
            for eid, incoming in frontier.items():
                rows = conn.execute(
                    "SELECT dst_entity_id AS other, weight FROM relation WHERE src_entity_id = ? "
                    "UNION ALL "
                    "SELECT src_entity_id AS other, weight FROM relation WHERE dst_entity_id = ?",
                    (eid, eid),
                ).fetchall()
                for row in rows:
                    other = row["other"]
                    if other in visited:
                        continue
                    score = incoming * row["weight"] / hop
                    if other not in next_frontier or score > next_frontier[other]:
                        next_frontier[other] = score
            for eid, score in next_frontier.items():
                if eid not in best or score > best[eid][0]:
                    best[eid] = (score, hop)
            visited |= set(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        if not best:
            return []
        ids = list(best)
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT entity_id, kind, canonical_name, salience FROM entity "
            "WHERE entity_id IN ({0})".format(placeholders),
            ids,
        ).fetchall()

    out = []
    for row in rows:
        item = dict(row)
        item["_edge_weight"], item["_hops"] = best[item["entity_id"]]
        out.append(item)
    out.sort(key=lambda item: item["_edge_weight"], reverse=True)
    return out


def emails_for_entity(entity_id: str, *, db_path: Optional[Path] = None) -> List[str]:
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT email_id FROM mention WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
    return [row["email_id"] for row in rows]


def load_all_vectors(*, db_path: Optional[Path] = None):
    """(chunk_ids, matrix) — one contiguous float32 matrix, not a list of
    arrays, per PHASES-COMPLEX.md B2's hot-path note. Vectors are stored
    pre-normalized (llm.embeddings.embed_texts' contract), so a caller can
    dot-product directly without re-normalizing."""
    if np is None:  # pragma: no cover - numpy is a declared dependency
        return [], None
    with db.connect(db_path) as conn:
        rows = conn.execute("SELECT chunk_id, vec FROM chunk_vec").fetchall()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    chunk_ids = [row["chunk_id"] for row in rows]
    matrix = np.stack([np.frombuffer(row["vec"], dtype="<f4") for row in rows])
    return chunk_ids, matrix
