"""Tests for context/embed.py — the pipeline's embed stage. No network."""

from __future__ import annotations

import unittest

from context import embed
from models.schema import Chunk, ChunkKind


def chunk(chunk_id: str, kind: ChunkKind, text: str = "some text") -> Chunk:
    return Chunk(chunk_id=chunk_id, email_id="e1", ord=0, text=text, kind=kind)


class EmbedChunksTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real = embed.embed_texts
        embed.embed_texts = self._fake

    def tearDown(self):
        embed.embed_texts = self._real

    def _fake(self, texts, model=None):
        self.sent.append(list(texts))
        return [bytes([index]) * 4 for index, _ in enumerate(texts)]

    def test_only_body_chunks_are_embedded(self):
        """Quoted history would make every message in a thread near-identical
        in vector space — the exact failure context.chunk exists to prevent."""
        pairs = embed.embed_chunks(
            [
                chunk("c0", ChunkKind.BODY, "real content"),
                chunk("c1", ChunkKind.QUOTED, "quoted history"),
                chunk("c2", ChunkKind.SIGNATURE, "phone number"),
            ]
        )
        self.assertEqual([cid for cid, _ in pairs], ["c0"])
        self.assertEqual(self.sent, [["real content"]])

    def test_no_body_chunks_makes_no_call(self):
        self.assertEqual(embed.embed_chunks([chunk("c1", ChunkKind.QUOTED)]), [])
        self.assertEqual(self.sent, [])

    def test_blank_body_chunk_is_skipped(self):
        self.assertEqual(embed.embed_chunks([chunk("c0", ChunkKind.BODY, "  ")]), [])
        self.assertEqual(self.sent, [])

    def test_empty_input(self):
        self.assertEqual(embed.embed_chunks([]), [])

    def test_pairs_line_up_with_their_chunks(self):
        pairs = embed.embed_chunks(
            [chunk("c0", ChunkKind.BODY, "a"), chunk("c1", ChunkKind.BODY, "b")]
        )
        self.assertEqual([cid for cid, _ in pairs], ["c0", "c1"])
        self.assertEqual(pairs[0][1], bytes([0]) * 4)
        self.assertEqual(pairs[1][1], bytes([1]) * 4)


if __name__ == "__main__":
    unittest.main()
