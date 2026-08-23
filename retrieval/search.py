"""Hybrid retrieval: BM25 (FTS5) + local-embedding vector search +
entity-graph walk, fused with Reciprocal Rank Fusion. PHASES-COMPLEX.md B2.

RRF, not a weighted sum of raw scores: BM25 scores, cosine similarities, and
graph edge weights aren't commensurable, and a weighted sum would let one
channel silently dominate. RRF only ever looks at each channel's rank order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from models import db

from . import _graph_read

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a declared dependency
    np = None

log = logging.getLogger(__name__)

_RRF_K = 60


@dataclass
class ScoredChunk:
    chunk_id: str
    email_id: str
    text: str
    score: float
    channel: str


def search(
    query: Optional[str] = None,
    *,
    k: int = 12,
    anchor_email_id: Optional[str] = None,
    filters: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> List[ScoredChunk]:
    """Fuse whichever channels have something to work with. `filters` is
    part of the frozen signature (PHASES-COMPLEX.md B2) but unused for now —
    no caller needs it yet; reserved rather than implemented speculatively.
    """
    del filters  # reserved, not yet used by any caller

    rankings: List[List[ScoredChunk]] = []
    if query:
        rankings.append(_bm25(query, k, db_path=db_path))
        rankings.append(_vector(query, k, db_path=db_path))
    if anchor_email_id:
        rankings.append(_graph(anchor_email_id, k, db_path=db_path))

    fused: Dict[str, float] = {}
    by_id: Dict[str, ScoredChunk] = {}
    channels_by_id: Dict[str, set] = {}
    for ranking in rankings:
        for rank, scored in enumerate(ranking, start=1):
            fused[scored.chunk_id] = fused.get(scored.chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
            by_id.setdefault(scored.chunk_id, scored)
            channels_by_id.setdefault(scored.chunk_id, set()).add(scored.channel)

    results = [
        ScoredChunk(
            chunk_id=cid,
            email_id=by_id[cid].email_id,
            text=by_id[cid].text,
            score=score,
            channel="+".join(sorted(channels_by_id[cid])),
        )
        for cid, score in fused.items()
    ]
    results.sort(key=lambda s: s.score, reverse=True)
    return results[:k]


# --- channels ----------------------------------------------------------------

def _fts_query(text: str) -> str:
    """Free text -> a safe FTS5 MATCH expression: each whitespace-separated
    token quoted as a literal phrase, so punctuation in the query (a hyphen
    in a ticket ID, say) is matched literally instead of parsed as FTS5
    query syntax. Space-separated quoted phrases still AND together."""
    tokens = text.split()
    return " ".join('"{0}"'.format(t.replace('"', '""')) for t in tokens)


def _bm25(query: str, k: int, *, db_path: Optional[Path] = None) -> List[ScoredChunk]:
    """The channel that nails exact IDs, names, and numbers."""
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT chunk.chunk_id, chunk.email_id, chunk.text, bm25(chunk_fts) AS rank "
            "FROM chunk_fts JOIN chunk ON chunk.rowid = chunk_fts.rowid "
            "WHERE chunk_fts MATCH ? AND chunk.kind = 'body' "
            "ORDER BY rank LIMIT ?",
            (_fts_query(query), k),
        ).fetchall()
    return [
        ScoredChunk(row["chunk_id"], row["email_id"], row["text"], -row["rank"], "bm25")
        for row in rows
    ]


# (db_path_key -> (chunk_ids, matrix)). Reloading the whole vector matrix per
# call is the obvious performance mistake here (PHASES-COMPLEX.md B2) — cache
# it, and give tests/callers an explicit way to invalidate it.
_vector_cache: Dict[str, tuple] = {}


def _cache_key(db_path: Optional[Path]) -> str:
    return str(db_path) if db_path is not None else ""


def invalidate_vector_cache(db_path: Optional[Path] = None) -> None:
    _vector_cache.pop(_cache_key(db_path), None)


def _load_vector_matrix(db_path: Optional[Path]):
    key = _cache_key(db_path)
    if key not in _vector_cache:
        _vector_cache[key] = _graph_read.load_all_vectors(db_path=db_path)
    return _vector_cache[key]


def _embed_query(query: str) -> Optional[bytes]:
    """Isolated to one function so tests can monkeypatch just this instead of
    reaching into llm.embeddings (Track A's A2 — see interfaces/README.md).
    Returns None when that module or its backend is unavailable; the vector
    channel then contributes nothing, the same graceful-degradation shape as
    every other cross-track call in this build."""
    try:
        from llm.embeddings import embed_texts
    except ImportError:
        return None
    try:
        return embed_texts([query])[0]
    except Exception as exc:  # noqa: BLE001 - any backend failure, not just one
        # Only the import was guarded before, so an unreachable embedding
        # backend (ollama not running) raised straight out of search() and
        # took BM25 and the graph walk down with it -- channels that need no
        # embeddings at all. Degrading to those is the whole point of a
        # hybrid retriever.
        log.warning("embedding backend unavailable, skipping vector channel: %s", exc)
        return None


def _vector(query: str, k: int, *, db_path: Optional[Path] = None) -> List[ScoredChunk]:
    if np is None:  # pragma: no cover - numpy is a declared dependency
        return []
    query_vec = _embed_query(query)
    if query_vec is None:
        return []
    chunk_ids, matrix = _load_vector_matrix(db_path)
    if not chunk_ids:
        return []

    query_arr = np.frombuffer(query_vec, dtype="<f4")
    if matrix.shape[1] != query_arr.shape[0]:
        # The corpus was embedded at a different width than the query — i.e.
        # LLM_EMBED_MODEL changed without a re-embed. Every stored vector is
        # stale, so the channel has nothing trustworthy to say; skip it
        # rather than raising out of search() and taking BM25 and the graph
        # walk down with it.
        return []
    # Vectors are pre-normalized on write (embed_texts' contract), so this
    # dot product is already cosine similarity — no re-normalizing needed.
    scores = matrix @ query_arr
    order = np.argsort(-scores)[:k]

    with db.connect(db_path) as conn:
        ids = [chunk_ids[i] for i in order]
        placeholders = ",".join("?" for _ in ids)
        rows = {
            row["chunk_id"]: row
            for row in conn.execute(
                "SELECT chunk_id, email_id, text FROM chunk "
                "WHERE chunk_id IN ({0}) AND kind = 'body'".format(placeholders),
                ids,
            ).fetchall()
        }

    results = []
    for i in order:
        cid = chunk_ids[i]
        row = rows.get(cid)
        if row is None:  # not a body chunk (quoted/signature) — skip
            continue
        results.append(ScoredChunk(cid, row["email_id"], row["text"], float(scores[i]), "vector"))
    return results


def _graph(anchor_email_id: str, k: int, *, db_path: Optional[Path] = None) -> List[ScoredChunk]:
    """entities_for_email -> neighbors -> emails_for_entity -> their chunks.
    The cross-thread correlation channel — the reason this project exists.
    One representative (lowest-`ord`, kind='body') chunk per graph-adjacent
    email, so this channel surfaces which OTHER emails are relevant without
    flooding results with every chunk of each one."""
    anchor_entities = _graph_read.entities_for_email(anchor_email_id, db_path=db_path)
    if not anchor_entities:
        return []

    scored_emails: Dict[str, float] = {}
    for entity in anchor_entities:
        salience = entity.get("salience") or 0.5
        for neighbor in _graph_read.neighbors(
            entity["entity_id"], hops=2, db_path=db_path
        ):
            weight = neighbor.get("_edge_weight", 0.0)
            hop = neighbor.get("_hops", 1)
            for email_id in _graph_read.emails_for_entity(
                neighbor["entity_id"], db_path=db_path
            ):
                if email_id == anchor_email_id:
                    continue
                decayed = salience * weight / hop
                scored_emails[email_id] = max(scored_emails.get(email_id, 0.0), decayed)

    if not scored_emails:
        return []

    with db.connect(db_path) as conn:
        placeholders = ",".join("?" for _ in scored_emails)
        rows = conn.execute(
            "SELECT chunk_id, email_id, text FROM chunk "
            "WHERE email_id IN ({0}) AND kind = 'body' "
            "ORDER BY email_id, ord".format(placeholders),
            list(scored_emails),
        ).fetchall()

    first_chunk_by_email = {}
    for row in rows:
        first_chunk_by_email.setdefault(row["email_id"], row)

    ranked = sorted(scored_emails.items(), key=lambda item: item[1], reverse=True)
    results = []
    for email_id, score in ranked:
        row = first_chunk_by_email.get(email_id)
        if row is None:
            continue
        results.append(ScoredChunk(row["chunk_id"], email_id, row["text"], score, "graph"))
        if len(results) >= k:
            break
    return results
