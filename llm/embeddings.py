"""Local text embeddings via ollama, stored as float32 BLOBs.

Vectors are one of two retrieval channels into the context graph (the other is
FTS5). They earn their place for two specific jobs — deciding that "Henderson
escalation" and "Henderson issue" are the same entity, and recalling related
mail when no shared identifier exists — not as the architecture itself. An
exact key beats cosine similarity whenever one is available.

Why BLOBs and numpy rather than a vector extension: this interpreter's sqlite3
is built without `enable_load_extension`, so sqlite-vec and sqlite-vss cannot
be loaded at all. At corpus scale that costs nothing. ~1,500 chunks x 768
dims x 4 bytes is ~4.6 MB, which is one contiguous matrix multiply — a few
milliseconds — so an ANN index would be complexity with no payoff.

VECTORS ARE L2-NORMALIZED ON WRITE. That is the load-bearing convention here:
it makes similarity at query time a plain dot product, which is what keeps the
retrieval layer's hot path a single `matrix @ query` instead of a per-row
division. Anything that writes a vector must go through `to_blob`.

Transport is deliberately not reimplemented — `_EmbeddingClient` subclasses
`llm.ollama.OllamaClient` so the host, the timeout, and the error shape are
the same ones every other local call already uses.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Any, Dict, List, Optional, Sequence

from . import config
from .ollama import OllamaClient, OllamaError

log = logging.getLogger(__name__)

# 768-dim, 274 MB. Not pulled by default — `check()` exists to say so clearly.
DEFAULT_MODEL = (os.environ.get("EMBED_MODEL") or "").strip() or "nomic-embed-text"

# One request per batch, not one per text: 1,500 separate HTTP round trips to
# localhost is minutes of pure overhead for a few seconds of inference.
BATCH_SIZE = int(os.environ.get("EMBED_BATCH") or 64)

# float32, little-endian. Fixed on disk, so a database stays readable on a
# big-endian host.
_STRUCT_FMT = "<{0}f"
_NUMPY_DTYPE = "<f4"


class EmbeddingError(OllamaError):
    """Embedding was unavailable or returned something unusable."""


def _np():
    """numpy, imported on demand.

    Deferred so that importing this module — which `pipeline.orchestrate` does
    transitively — does not pay for numpy on a run with no vector work.
    """
    import numpy

    return numpy


class _EmbeddingClient(OllamaClient):
    """OllamaClient's transport, pointed at the /api/embed endpoint.

    Subclassed rather than copied: `_post` already handles a refused
    connection, an HTTP error, and a non-JSON body with the messages this
    repo's users have already learned to read.
    """

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        data = self._post("/api/embed", {"model": self.model, "input": list(texts)})
        vectors = data.get("embeddings")
        if not vectors:
            # ollama answers 200 with an empty body when the model exists but
            # cannot embed, and 404 when it is not pulled — the latter is
            # already an OllamaError from _post.
            raise EmbeddingError(
                "ollama returned no embeddings for model {0!r}. Try: "
                "ollama pull {0}".format(self.model)
            )
        if len(vectors) != len(texts):
            raise EmbeddingError(
                "ollama returned {0} embeddings for {1} inputs".format(
                    len(vectors), len(texts)
                )
            )
        return vectors


def _client(model: Optional[str] = None, session: Optional[Any] = None) -> _EmbeddingClient:
    return _EmbeddingClient(
        host=config.OLLAMA_HOST,
        model=model or DEFAULT_MODEL,
        timeout=config.OLLAMA_TIMEOUT,
        session=session,
    )


# --- blob format ----------------------------------------------------------

def to_blob(vec: Sequence[float]) -> bytes:
    """L2-normalize and pack as float32 little-endian.

    The single place normalization happens, so "normalized on write" is a
    property of the format rather than a rule every caller has to remember.
    Idempotent — normalizing an already-unit vector is a no-op.
    """
    values = [float(v) for v in vec]
    norm = sum(v * v for v in values) ** 0.5
    if norm > 0:
        values = [v / norm for v in values]
    return struct.pack(_STRUCT_FMT.format(len(values)), *values)


def from_blob(blob: bytes) -> "Any":
    """Unpack a stored blob into a 1-D float32 numpy array."""
    return _np().frombuffer(blob, dtype=_NUMPY_DTYPE)


def dim(blob: bytes) -> int:
    """Dimensionality of a stored blob, without unpacking it."""
    return len(blob) // 4


# --- similarity -----------------------------------------------------------

def cosine(a: bytes, b: bytes) -> float:
    """True cosine similarity between two stored vectors.

    Divides by the norms rather than assuming them, even though `to_blob`
    normalizes: this is the function tuning tests call with hand-built
    vectors, and a silently-wrong answer there would be tuned around instead
    of noticed.
    """
    np = _np()
    x, y = from_blob(a), from_blob(b)
    if x.shape != y.shape:
        raise EmbeddingError(
            "cannot compare vectors of different dimension: {0} vs {1}".format(
                x.shape[0], y.shape[0]
            )
        )
    denominator = float(np.linalg.norm(x)) * float(np.linalg.norm(y))
    if denominator == 0:
        return 0.0
    return float(np.dot(x, y) / denominator)


def cosine_matrix(query: bytes, matrix: "Any") -> "Any":
    """Similarity of `query` against every row of `matrix`, as a 1-D array.

    The retrieval layer's hot path. Rows are assumed already normalized —
    that is what `to_blob` guarantees and what `context.store.load_all_vectors`
    returns — so this is one matrix-vector product with no per-row division.
    Only the query is normalized here, which is O(dim) and free.
    """
    np = _np()
    if matrix is None or getattr(matrix, "size", 0) == 0:
        return np.zeros(0, dtype="float32")
    vector = from_blob(query).astype("float32")
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    return np.asarray(matrix, dtype="float32") @ vector


# --- embedding ------------------------------------------------------------

def embed_texts(
    texts: Sequence[str],
    *,
    model: str = DEFAULT_MODEL,
    batch_size: int = BATCH_SIZE,
    session: Optional[Any] = None,
) -> List[bytes]:
    """One normalized blob per input text, in input order.

    Blank inputs are not sent: ollama's behavior on an empty string varies by
    model, and a zero vector is the honest answer for "no text" — it scores 0
    against everything rather than accidentally matching whatever the model
    emits for "".
    """
    texts = list(texts)
    if not texts:
        return []

    payload_indices = [i for i, text in enumerate(texts) if (text or "").strip()]
    if not payload_indices:
        return [b"" for _ in texts]

    client = _client(model, session)
    vectors: List[List[float]] = []
    for start in range(0, len(payload_indices), max(1, batch_size)):
        batch = [texts[i] for i in payload_indices[start : start + batch_size]]
        vectors.extend(client.embed(batch))

    width = len(vectors[0]) if vectors else 0
    out: List[bytes] = [to_blob([0.0] * width) for _ in texts]
    for index, vector in zip(payload_indices, vectors):
        out[index] = to_blob(vector)
    return out


def embed_one(text: str, *, model: str = DEFAULT_MODEL, session: Optional[Any] = None) -> bytes:
    """One text, one blob. The query side of the vector channel."""
    return embed_texts([text], model=model, session=session)[0]


# --- diagnostics ----------------------------------------------------------

def check(*, model: str = DEFAULT_MODEL, session: Optional[Any] = None) -> str:
    """One human-readable line about whether embedding will work.

    Distinguishes "the server is not running" from "the model is not pulled"
    on purpose, modelled on OllamaClient.check: they are completely different
    fixes, and somebody will forget the pull. Both otherwise surface as the
    same opaque failure ten minutes later, in the middle of a 160-email run.
    """
    client = _client(model, session)
    try:
        available = client.available_models()
    except OllamaError as exc:
        return (
            "UNAVAILABLE: ollama is not reachable at {0} ({1}). Start it with "
            "`ollama serve`.".format(client.host, exc)
        )

    present = any(
        name == client.model or name.split(":")[0] == client.model.split(":")[0]
        for name in available
    )
    if not present:
        return (
            "UNAVAILABLE: ollama is running at {0}, but the embedding model "
            "{1!r} is not pulled. Run: ollama pull {1}".format(client.host, client.model)
        )

    try:
        probe = client.embed(["probe"])[0]
    except OllamaError as exc:
        return "UNAVAILABLE: {0} is pulled but embedding failed: {1}".format(
            client.model, exc
        )
    return "OK: {0} at {1}, {2} dimensions".format(client.model, client.host, len(probe))
