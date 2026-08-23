"""Tests for llm/embeddings.py. No network — the HTTP session is injected.

The blob format and the normalize-on-write rule are what the whole retrieval
layer's fast path assumes, so they are asserted directly rather than through
a caller.
"""

from __future__ import annotations

import json
import unittest

from llm import embeddings
from llm.ollama import OllamaError


class _Response:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    """Records requests and replays scripted responses. No sockets."""

    def __init__(self, *responses, tags=None, raises=None):
        self._responses = list(responses)
        self._tags = tags
        self._raises = raises
        self.posts = []
        self.gets = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json})
        if self._raises:
            raise self._raises
        payload = (
            self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)
        )
        if isinstance(payload, BaseException):
            raise payload
        return _Response(payload)

    def get(self, url, timeout=None):
        self.gets.append(url)
        if self._tags is None:
            raise OSError("connection refused")
        return _Response({"models": [{"name": name} for name in self._tags]})


def embed_response(*vectors):
    return {"embeddings": [list(v) for v in vectors]}


class BlobFormatTest(unittest.TestCase):
    def test_round_trip(self):
        blob = embeddings.to_blob([0.6, 0.8, 0.0])
        restored = embeddings.from_blob(blob)
        self.assertEqual(embeddings.dim(blob), 3)
        self.assertAlmostEqual(float(restored[0]), 0.6, places=6)
        self.assertAlmostEqual(float(restored[1]), 0.8, places=6)

    def test_to_blob_normalizes(self):
        """Normalize-on-write is what makes the read path a plain dot product,
        so it belongs to the format, not to each caller."""
        restored = embeddings.from_blob(embeddings.to_blob([3.0, 4.0]))
        self.assertAlmostEqual(float((restored ** 2).sum()), 1.0, places=5)
        self.assertAlmostEqual(float(restored[0]), 0.6, places=5)

    def test_to_blob_is_idempotent(self):
        once = embeddings.to_blob([3.0, 4.0])
        twice = embeddings.to_blob(embeddings.from_blob(once))
        self.assertEqual(once, twice)

    def test_zero_vector_does_not_divide_by_zero(self):
        blob = embeddings.to_blob([0.0, 0.0, 0.0])
        self.assertEqual(embeddings.dim(blob), 3)
        self.assertEqual(embeddings.cosine(blob, blob), 0.0)


class CosineTest(unittest.TestCase):
    def test_identical_vectors_score_one(self):
        blob = embeddings.to_blob([1.0, 2.0, 3.0])
        self.assertAlmostEqual(embeddings.cosine(blob, blob), 1.0, places=6)

    def test_orthogonal_vectors_score_zero(self):
        a = embeddings.to_blob([1.0, 0.0])
        b = embeddings.to_blob([0.0, 1.0])
        self.assertAlmostEqual(embeddings.cosine(a, b), 0.0, places=6)

    def test_opposite_vectors_score_minus_one(self):
        a = embeddings.to_blob([1.0, 0.0])
        b = embeddings.to_blob([-1.0, 0.0])
        self.assertAlmostEqual(embeddings.cosine(a, b), -1.0, places=6)

    def test_unnormalized_input_still_gives_true_cosine(self):
        """cosine divides by the norms rather than assuming them: this is the
        function threshold-tuning tests call with hand-built vectors."""
        import numpy

        a = numpy.array([3.0, 4.0], dtype="<f4").tobytes()
        b = numpy.array([6.0, 8.0], dtype="<f4").tobytes()
        self.assertAlmostEqual(embeddings.cosine(a, b), 1.0, places=6)

    def test_mismatched_dimensions_raise(self):
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.cosine(embeddings.to_blob([1.0]), embeddings.to_blob([1.0, 0.0]))

    def test_cosine_matrix_scores_every_row(self):
        import numpy

        matrix = numpy.array(
            [[1.0, 0.0], [0.0, 1.0], [0.7071068, 0.7071068]], dtype="float32"
        )
        scores = embeddings.cosine_matrix(embeddings.to_blob([1.0, 0.0]), matrix)
        self.assertEqual(list(scores.shape), [3])
        self.assertAlmostEqual(float(scores[0]), 1.0, places=5)
        self.assertAlmostEqual(float(scores[1]), 0.0, places=5)
        self.assertAlmostEqual(float(scores[2]), 0.7071, places=3)

    def test_cosine_matrix_on_empty_matrix(self):
        import numpy

        empty = numpy.zeros((0, 4), dtype="float32")
        self.assertEqual(
            embeddings.cosine_matrix(embeddings.to_blob([1.0, 0, 0, 0]), empty).size, 0
        )


class EmbedTextsTest(unittest.TestCase):
    def test_posts_to_api_embed_and_returns_blobs(self):
        session = FakeSession(embed_response([3.0, 4.0], [1.0, 0.0]))
        blobs = embeddings.embed_texts(["a", "b"], session=session)
        self.assertEqual(len(session.posts), 1)
        self.assertTrue(session.posts[0]["url"].endswith("/api/embed"))
        self.assertEqual(session.posts[0]["json"]["input"], ["a", "b"])
        self.assertAlmostEqual(float(embeddings.from_blob(blobs[0])[0]), 0.6, places=5)

    def test_batches_instead_of_one_call_per_text(self):
        """1,500 separate round trips to localhost is minutes of pure overhead."""
        session = FakeSession(
            embed_response([1.0, 0.0], [0.0, 1.0]),
            embed_response([1.0, 1.0]),
        )
        blobs = embeddings.embed_texts(["a", "b", "c"], batch_size=2, session=session)
        self.assertEqual(len(blobs), 3)
        self.assertEqual(len(session.posts), 2)
        self.assertEqual(session.posts[0]["json"]["input"], ["a", "b"])
        self.assertEqual(session.posts[1]["json"]["input"], ["c"])

    def test_blank_text_is_not_sent_and_keeps_its_position(self):
        session = FakeSession(embed_response([1.0, 0.0], [0.0, 1.0]))
        blobs = embeddings.embed_texts(["a", "   ", "b"], session=session)
        self.assertEqual(session.posts[0]["json"]["input"], ["a", "b"])
        self.assertEqual(len(blobs), 3)
        self.assertEqual(embeddings.dim(blobs[1]), 2)
        self.assertEqual(embeddings.cosine(blobs[1], blobs[1]), 0.0)

    def test_no_texts_makes_no_request(self):
        session = FakeSession(embed_response())
        self.assertEqual(embeddings.embed_texts([], session=session), [])
        self.assertEqual(session.posts, [])

    def test_wrong_count_back_from_the_server_raises(self):
        session = FakeSession(embed_response([1.0, 0.0]))
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.embed_texts(["a", "b"], session=session)

    def test_empty_embeddings_names_the_pull_command(self):
        session = FakeSession({"embeddings": []})
        with self.assertRaises(embeddings.EmbeddingError) as caught:
            embeddings.embed_texts(["a"], session=session)
        self.assertIn("ollama pull", str(caught.exception))


class CheckTest(unittest.TestCase):
    def test_server_down(self):
        message = embeddings.check(session=FakeSession(tags=None))
        self.assertIn("not reachable", message)
        self.assertIn("ollama serve", message)

    def test_model_not_pulled_names_the_pull_command(self):
        """Different fix from a dead server, and somebody will forget the pull."""
        message = embeddings.check(session=FakeSession(tags=["gemma2:2b"]))
        self.assertIn("not pulled", message)
        self.assertIn("ollama pull nomic-embed-text", message)

    def test_ok_reports_dimensions(self):
        session = FakeSession(
            embed_response([0.0] * 768), tags=["nomic-embed-text:latest"]
        )
        message = embeddings.check(session=session)
        self.assertTrue(message.startswith("OK:"))
        self.assertIn("768 dimensions", message)

    def test_pulled_but_failing_is_distinguished_from_both(self):
        session = FakeSession({"embeddings": []}, tags=["nomic-embed-text"])
        message = embeddings.check(session=session)
        self.assertIn("is pulled but embedding failed", message)


if __name__ == "__main__":
    unittest.main()
