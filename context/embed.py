"""The `embed` context stage: chunks in, (chunk_id, vector) pairs out.

A thin adapter, on purpose. `llm/embeddings.py` is a transport client that
knows nothing about this repo's types, and `pipeline.orchestrate` injects one
callable per stage; this is the seam between them. It also enforces the rule
that only BODY chunks are ever embedded — quoted reply history would make
every message in a thread near-identical in vector space, which is the exact
failure `context.chunk` exists to prevent, and re-admitting it here would undo
that work one layer down.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from llm.embeddings import DEFAULT_MODEL, embed_texts
from models.schema import Chunk, ChunkKind


def embed_chunks(
    chunks: Sequence[Chunk],
    *,
    model: str = DEFAULT_MODEL,
) -> List[Tuple[str, bytes]]:
    """(chunk_id, normalized float32 blob) for the BODY chunks in `chunks`.

    Non-BODY chunks are dropped rather than embedded, so a caller may hand
    this every chunk of an email without having to filter first.
    """
    body = [c for c in chunks if c.kind == ChunkKind.BODY and (c.text or "").strip()]
    if not body:
        return []
    blobs = embed_texts([c.text for c in body], model=model)
    return [(chunk.chunk_id, blob) for chunk, blob in zip(body, blobs) if blob]
