"""Tests for the context pass added in Checkpoint 0 — the two-pass split.

Covers `pipeline.orchestrate`'s CONTEXT_STAGES wiring and
`pipeline.incremental`'s context planner. No network, no model, no DB: every
stage is an injected fake, and the planner takes its coverage as an argument.

The property these exist to protect is the ordering rule from
PHASES-COMPLEX.md §2 — context stages must not run inside `process_one`,
because that is exactly the interleaving that would let email #1's outline be
generated against a graph containing only email #1.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from models.schema import (
    Chunk,
    ChunkKind,
    Mention,
    MentionSource,
    ProcessedEmail,
    RawEmail,
    ReadStatus,
)
from pipeline import incremental
from pipeline.orchestrate import (
    ALL_STAGE_NAMES,
    CONTEXT_STAGES,
    STAGES,
    ContextResult,
    Pipeline,
)


def raw(email_id="e1", read_status=ReadStatus.READ) -> RawEmail:
    return RawEmail(
        email_id=email_id,
        thread_id="t1",
        sender="a@b.com",
        recipients=["me@example.com"],
        subject="Subject",
        body="Body text.",
        received_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        read_status=read_status,
        headers={},
    )


def chunks_for(email: RawEmail):
    return [
        Chunk(chunk_id=email.email_id + ":0", email_id=email.email_id, ord=0,
              text="body", kind=ChunkKind.BODY),
        Chunk(chunk_id=email.email_id + ":1", email_id=email.email_id, ord=1,
              text="> quoted", kind=ChunkKind.QUOTED),
    ]


class StageConstantsTest(unittest.TestCase):
    def test_context_stages_are_not_in_stages(self):
        """Appending them to STAGES would put extraction and outline generation
        in the same per-email pass, which is the bug the split prevents."""
        self.assertEqual(set(CONTEXT_STAGES) & set(STAGES), set())

    def test_all_stage_names_is_the_union_context_first(self):
        self.assertEqual(ALL_STAGE_NAMES, tuple(CONTEXT_STAGES) + tuple(STAGES))

    def test_the_existing_reasoning_stage_order_is_unchanged(self):
        self.assertEqual(
            STAGES,
            ("classify", "score", "summarize", "action_items", "categorize",
             "scheduling", "calendar", "propose_event", "outline", "expand"),
        )


class RunContextTest(unittest.TestCase):
    def setUp(self):
        self.embedded = []
        self.extracted = []
        self.pipeline = Pipeline(
            chunk=chunks_for,
            embed=self._embed,
            extract=self._extract,
            stages=CONTEXT_STAGES,
        )

    def _embed(self, chunks):
        self.embedded.append([c.chunk_id for c in chunks])
        return [(c.chunk_id, b"vec!") for c in chunks]

    def _extract(self, email, chunks):
        self.extracted.append([c.chunk_id for c in chunks])
        return [
            Mention(email_id=email.email_id, entity_id="case:CS1",
                    span_text="CS-1", source=MentionSource.REGEX)
        ]

    def test_returns_chunks_vectors_and_mentions(self):
        result = self.pipeline.run_context_one(raw())
        self.assertEqual(len(result.chunks), 2)
        self.assertEqual(result.vectors, [("e1:0", b"vec!")])
        self.assertEqual([m.entity_id for m in result.mentions], ["case:CS1"])

    def test_only_body_chunks_reach_embed_and_extract(self):
        """Quoted history would make every message in a thread near-identical
        in vector space, and would credit the quoted author's entities to
        whoever replied below them."""
        self.pipeline.run_context_one(raw())
        self.assertEqual(self.embedded, [["e1:0"]])
        self.assertEqual(self.extracted, [["e1:0"]])

    def test_chunking_still_runs_when_only_the_costly_stages_are_disabled(self):
        """Chunking is pure string work with no network and no model, and both
        expensive stages take chunks as input — gating it saves nothing."""
        pipeline = Pipeline(
            chunk=chunks_for, embed=self._embed, extract=self._extract,
            stages=("chunk",),
        )
        result = pipeline.run_context_one(raw())
        self.assertEqual(len(result.chunks), 2)
        self.assertEqual(result.vectors, [])
        self.assertEqual(result.mentions, [])
        self.assertEqual(self.embedded, [])

    def test_no_context_stages_enabled_does_nothing(self):
        pipeline = Pipeline(chunk=chunks_for, embed=self._embed, stages=STAGES)
        result = pipeline.run_context_one(raw())
        self.assertEqual(result, ContextResult(email_id="e1"))

    def test_a_failing_stage_does_not_abort_the_email(self):
        """One email that fails extraction must not cost the other 159 theirs."""
        def explode(*_args):
            raise RuntimeError("model timed out")

        pipeline = Pipeline(
            chunk=chunks_for, embed=self._embed, extract=explode, stages=CONTEXT_STAGES
        )
        result = pipeline.run_context_one(raw())
        self.assertEqual(len(result.chunks), 2)
        self.assertEqual(result.mentions, [])
        self.assertEqual(len(pipeline.errors), 1)
        self.assertIn("extract failed for e1", pipeline.errors[0])

    def test_a_failing_chunk_stage_leaves_an_empty_result(self):
        def explode(*_args):
            raise RuntimeError("bad body")

        pipeline = Pipeline(chunk=explode, embed=self._embed, stages=CONTEXT_STAGES)
        result = pipeline.run_context_one(raw())
        self.assertEqual(result.chunks, [])
        self.assertEqual(self.embedded, [[]])

    def test_batch_reports_progress_per_email(self):
        seen = []
        results = self.pipeline.run_context(
            [raw("e1"), raw("e2"), raw("e3")], on_progress=lambda d, t: seen.append((d, t))
        )
        self.assertEqual([r.email_id for r in results], ["e1", "e2", "e3"])
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_process_one_never_runs_a_context_stage(self):
        """The ordering rule, asserted directly: a pipeline with every stage
        enabled must still not chunk during the reasoning pass."""
        calls = []
        pipeline = Pipeline(
            chunk=lambda r: calls.append("chunk") or [],
            classify=lambda r: (False, "personal"),
            stages=tuple(ALL_STAGE_NAMES),
        )
        pipeline.process_one(raw())
        self.assertEqual(calls, [])


class WithDefaultsTest(unittest.TestCase):
    def test_a_reasoning_only_run_does_not_wire_the_context_stages(self):
        """Every existing caller takes this path; it must not pay to import
        numpy and the embeddings client, nor fail if context/ is unavailable."""
        pipeline = Pipeline.with_defaults()
        self.assertIsNone(pipeline._chunk)
        self.assertIsNone(pipeline._embed)
        self.assertIsNone(pipeline._extract)

    def test_a_context_run_wires_them(self):
        pipeline = Pipeline.with_defaults(stages=CONTEXT_STAGES)
        self.assertIsNotNone(pipeline._chunk)
        self.assertIsNotNone(pipeline._embed)
        self.assertIsNotNone(pipeline._extract)
        self.assertIsNotNone(pipeline._classify, "reasoning stages stay wired")


class ContextPlannerTest(unittest.TestCase):
    def test_nothing_done_means_every_stage(self):
        self.assertEqual(
            incremental.context_stages_for("e1", {}), tuple(CONTEXT_STAGES)
        )

    def test_partially_done_means_the_remainder(self):
        coverage = {"chunk": {"e1"}, "embed": {"e1"}, "extract": set()}
        self.assertEqual(incremental.context_stages_for("e1", coverage), ("extract",))

    def test_fully_done_means_nothing(self):
        coverage = {stage: {"e1"} for stage in CONTEXT_STAGES}
        self.assertEqual(incremental.context_stages_for("e1", coverage), ())

    def test_returned_stages_keep_canonical_order(self):
        coverage = {"chunk": set(), "embed": set(), "extract": set()}
        self.assertEqual(
            incremental.context_stages_for("e1", coverage), ("chunk", "embed", "extract")
        )

    def test_plan_skips_the_emails_that_are_complete(self):
        coverage = {stage: {"e1"} for stage in CONTEXT_STAGES}
        plan = incremental.context_plan([raw("e1"), raw("e2")], coverage)
        self.assertEqual(list(plan), ["e2"])

    def test_a_read_flip_does_not_invalidate_the_context_pass(self):
        """The body did not change by one character. Re-chunking,
        re-embedding, and re-extracting would be paid work for a
        guaranteed-identical result; `stages_for` handles the flip by
        re-running "outline" alone and this must not undo that."""
        coverage = {stage: {"e1"} for stage in CONTEXT_STAGES}
        for status in (ReadStatus.READ, ReadStatus.UNREAD):
            with self.subTest(status):
                self.assertEqual(
                    incremental.context_plan([raw("e1", status)], coverage), {}
                )

    def test_a_read_flip_still_re_runs_the_outline_stage(self):
        existing = ProcessedEmail(
            email_id="e1", thread_id="t1", sender="a@b.com", subject="s",
            received_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
            read_status=ReadStatus.UNREAD, is_no_reply=False, importance_score=50.0,
            summary="s", category="c", is_scheduling_related=False,
            processed_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        )
        self.assertIn("outline", incremental.stages_for(raw("e1", ReadStatus.READ), existing))

    def test_summarize_plan_reads_a_context_plan(self):
        line = incremental.summarize_plan(
            {"e1": ("chunk", "embed", "extract"), "e2": ("extract",)}, 10
        )
        self.assertIn("2/10", line)
        self.assertIn("chunkx1", line)
        self.assertIn("extractx2", line)

    def test_stage_table_matches_the_stage_tuple(self):
        self.assertEqual(
            set(incremental.CONTEXT_STAGE_TABLE), set(CONTEXT_STAGES)
        )


if __name__ == "__main__":
    unittest.main()
