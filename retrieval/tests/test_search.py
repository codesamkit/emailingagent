"""PHASES-COMPLEX.md B2: the key assertion is the same-case/no-shared-
vocabulary scenario — for a query naming the case only by its human label,
all three Henderson emails (h1a/h1b by BM25, h2 by vector, h3 by graph-only)
come back, and at least one hit is NOT from the BM25 channel."""

from __future__ import annotations

import struct

from retrieval import search
from retrieval.tests.fixtures import build_fixture_db

# The h1a/h1b/h2 8-dim vectors in fixtures.py are all dim0-heavy ("henderson/
# incident" theme) — this stands in for embedding the words "Henderson
# escalation" and landing near those chunks semantically, without needing a
# live Ollama call in tests.
_QUERY_VEC = struct.pack("<8f", 0.9, 0.25, 0, 0, 0, 0, 0, 0.05)


def _patched_embed(monkeypatch, vec: bytes = _QUERY_VEC):
    monkeypatch.setattr(search, "_embed_query", lambda query: vec)


def test_same_case_no_shared_vocabulary_scenario(monkeypatch):
    path = build_fixture_db()
    _patched_embed(monkeypatch)
    search.invalidate_vector_cache(path)

    results = search.search("Henderson escalation", k=20, db_path=path)
    hit_emails = {r.email_id for r in results}

    assert {"email-h1a", "email-h1b", "email-h2"} <= hit_emails
    channels = {r.channel for r in results if r.email_id in hit_emails}
    assert any(c != "bm25" for c in channels)


def test_graph_channel_alone_surfaces_the_unnamed_email():
    path = build_fixture_db()
    results = search._graph("email-h1a", k=20, db_path=path)
    assert "email-h3" in {r.email_id for r in results}
    assert all(r.channel == "graph" for r in results)


def test_bm25_alone_does_not_find_the_unnamed_email():
    path = build_fixture_db()
    results = search._bm25("Henderson", k=20, db_path=path)
    assert "email-h3" not in {r.email_id for r in results}


def test_vector_cache_is_reused_across_calls(monkeypatch):
    path = build_fixture_db()
    _patched_embed(monkeypatch)
    search.invalidate_vector_cache(path)

    calls = []
    real_loader = search._graph_read.load_all_vectors

    def _counting_loader(*, db_path=None):
        calls.append(1)
        return real_loader(db_path=db_path)

    monkeypatch.setattr(search._graph_read, "load_all_vectors", _counting_loader)

    search._vector("Henderson escalation", k=5, db_path=path)
    search._vector("Henderson escalation", k=5, db_path=path)
    assert len(calls) == 1


def test_search_with_no_query_or_anchor_returns_nothing():
    path = build_fixture_db()
    assert search.search(db_path=path) == []


def test_rrf_does_not_error_when_embeddings_are_unavailable():
    """If llm.embeddings isn't importable (Person A's track not built yet in
    this repo), the vector channel contributes nothing rather than raising —
    BM25 and graph still work."""
    path = build_fixture_db()
    results = search.search("Henderson escalation", k=20, db_path=path)
    assert {"email-h1a", "email-h1b"} <= {r.email_id for r in results}
