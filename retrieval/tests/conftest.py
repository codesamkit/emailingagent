"""Keeps the retrieval suite hermetic.

`retrieval.search._embed_query` reaches into llm.embeddings, which now
exists (Track A) and calls a local Ollama model. Left unstubbed, every test
that passes a query would make a live model call and then compare a
768-dim query against this suite's 8-dim fixture vectors. Default the
channel to unavailable — the documented degradation path — and let the
tests that actually exercise vector ranking opt in by monkeypatching
`_embed_query` themselves (see test_search.py's _patched_embed).
"""

from __future__ import annotations

import pytest

from retrieval import search


@pytest.fixture(autouse=True)
def _no_live_embeddings(monkeypatch):
    monkeypatch.setattr(search, "_embed_query", lambda query: None)
