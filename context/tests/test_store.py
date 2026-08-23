"""Tests for context/store.py. Real SQLite, temp file, no network, no model.

A temp file rather than ":memory:" because every function opens its own
connection through models.db.connect — an in-memory database would be a
different, empty database each time, which is exactly the plumbing these
tests exist to check.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from context import store
from context.resolve import entity_id_for
from context.tests.fakes import vec
from llm.embeddings import to_blob
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


def chunk(chunk_id, email_id="e1", ord=0, text="body text", kind=ChunkKind.BODY):
    return Chunk(chunk_id=chunk_id, email_id=email_id, ord=ord, text=text, kind=kind)


def entity(kind, key, name=None, **kwargs):
    return Entity(
        entity_id=entity_id_for(kind, key),
        kind=kind,
        canonical_name=name or key,
        normalized_key=key,
        **kwargs
    )


def mention(entity_id, email_id="e1", span="span", **kwargs):
    return Mention(
        email_id=email_id,
        entity_id=entity_id,
        span_text=span,
        source=kwargs.pop("source", MentionSource.REGEX),
        **kwargs
    )


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = Path(self._dir.name) / "test.db"
        store.init_db(self.db)

    def tearDown(self):
        self._dir.cleanup()

    def add_raw(self, email_id, thread_id="t1", received_at="2026-08-20T09:00:00+00:00"):
        with db.connect(self.db) as conn:
            db.prepare(conn)
            conn.execute(
                "INSERT OR REPLACE INTO raw_email (email_id, thread_id, sender,"
                " recipients, subject, received_at, read_status, label_ids, headers,"
                " fetched_at) VALUES (?,?,?,'[]','s',?, 'read','[]','{}',?)",
                (email_id, thread_id, "a@b.com", received_at, received_at),
            )
            conn.commit()


class ChunkTest(StoreTestCase):
    def test_round_trip_and_kind_filter(self):
        store.upsert_chunks(
            [chunk("e1:0"), chunk("e1:1", kind=ChunkKind.QUOTED, ord=1)], db_path=self.db
        )
        self.assertEqual(len(store.chunks_for_email("e1", db_path=self.db)), 2)
        body = store.chunks_for_email("e1", kind=ChunkKind.BODY, db_path=self.db)
        self.assertEqual([c.chunk_id for c in body], ["e1:0"])

    def test_rechunking_replaces_rather_than_accumulating(self):
        """Re-chunking with a bigger target legitimately produces FEWER rows.
        A pure upsert keyed on chunk_id would leave the surplus in the FTS
        index forever, still answering searches."""
        store.upsert_chunks(
            [chunk("e1:0"), chunk("e1:1", ord=1), chunk("e1:2", ord=2)], db_path=self.db
        )
        store.upsert_chunks([chunk("e1:0", text="merged")], db_path=self.db)
        remaining = store.chunks_for_email("e1", db_path=self.db)
        self.assertEqual([c.chunk_id for c in remaining], ["e1:0"])
        self.assertEqual(remaining[0].text, "merged")

    def test_replacing_chunks_drops_all_of_that_emails_vectors(self):
        """Every vector goes, not only the ones whose chunk disappeared.

        Nothing enforces this — the schema has no foreign keys — and a chunk_id
        that survives a re-chunk usually holds DIFFERENT text, so its old
        vector is stale rather than reusable. Keeping it would be a silent
        wrong answer in the vector channel; re-embedding is local and free, and
        `context_coverage` puts the email back in the embed queue automatically.
        """
        store.upsert_chunks([chunk("e1:0"), chunk("e1:1", ord=1)], db_path=self.db)
        store.upsert_vectors(
            [("e1:0", to_blob([1, 0])), ("e1:1", to_blob([0, 1]))], db_path=self.db
        )
        self.assertEqual(store.counts(db_path=self.db)["chunk_vec"], 2)
        store.upsert_chunks([chunk("e1:0", text="different text now")], db_path=self.db)
        self.assertEqual(store.counts(db_path=self.db)["chunk_vec"], 0)
        self.assertEqual(store.context_coverage(db_path=self.db)["embed"], set())

    def test_other_emails_are_untouched(self):
        store.upsert_chunks([chunk("e1:0"), chunk("e2:0", email_id="e2")], db_path=self.db)
        store.upsert_chunks([chunk("e1:0", text="new")], db_path=self.db)
        self.assertEqual(len(store.chunks_for_email("e2", db_path=self.db)), 1)

    def test_fts_index_tracks_replacement(self):
        store.upsert_chunks([chunk("e1:0", text="Henderson escalation")], db_path=self.db)
        with db.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) n FROM chunk_fts WHERE chunk_fts MATCH 'Henderson'"
                ).fetchone()["n"],
                1,
            )
        store.upsert_chunks([chunk("e1:0", text="something else entirely")], db_path=self.db)
        with db.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) n FROM chunk_fts WHERE chunk_fts MATCH 'Henderson'"
                ).fetchone()["n"],
                0,
            )

    def test_empty_input_is_a_no_op(self):
        self.assertEqual(store.upsert_chunks([], db_path=self.db), 0)


class VectorTest(StoreTestCase):
    def test_load_all_vectors_returns_one_contiguous_matrix(self):
        store.upsert_chunks([chunk("e1:0"), chunk("e1:1", ord=1)], db_path=self.db)
        store.upsert_vectors(
            [("e1:0", to_blob([1, 0, 0])), ("e1:1", to_blob([0, 1, 0]))], db_path=self.db
        )
        ids, matrix = store.load_all_vectors(db_path=self.db)
        self.assertEqual(ids, ["e1:0", "e1:1"])
        self.assertEqual(matrix.shape, (2, 3))
        self.assertTrue(matrix.flags["C_CONTIGUOUS"])

    def test_empty_store(self):
        ids, matrix = store.load_all_vectors(db_path=self.db)
        self.assertEqual(ids, [])
        self.assertEqual(matrix.size, 0)

    def test_minority_dimension_is_dropped_not_fatal(self):
        """A corpus embedded with two models should degrade to the majority,
        not fail every search."""
        store.upsert_chunks(
            [chunk("e1:0"), chunk("e1:1", ord=1), chunk("e1:2", ord=2)], db_path=self.db
        )
        store.upsert_vectors(
            [
                ("e1:0", to_blob([1, 0, 0])),
                ("e1:1", to_blob([0, 1, 0])),
                ("e1:2", to_blob([1, 0])),
            ],
            db_path=self.db,
        )
        ids, matrix = store.load_all_vectors(db_path=self.db)
        self.assertEqual(matrix.shape, (2, 3))
        self.assertNotIn("e1:2", ids)

    def test_upsert_overwrites(self):
        store.upsert_chunks([chunk("e1:0")], db_path=self.db)
        store.upsert_vectors([("e1:0", to_blob([1, 0]))], db_path=self.db)
        store.upsert_vectors([("e1:0", to_blob([0, 1]))], db_path=self.db)
        _, matrix = store.load_all_vectors(db_path=self.db)
        self.assertEqual(matrix.shape, (1, 2))
        self.assertAlmostEqual(float(matrix[0][1]), 1.0, places=5)


class EntityAndMentionTest(StoreTestCase):
    def test_entities_and_aliases_round_trip(self):
        atlas = entity(EntityKind.PROJECT, "atlas", "Atlas", aliases=["Atlas Programme"],
                       mention_count=4, salience=0.5)
        store.upsert_entities([atlas], db_path=self.db)
        loaded = store.all_entities(kind=EntityKind.PROJECT, db_path=self.db)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].canonical_name, "Atlas")
        self.assertEqual(loaded[0].mention_count, 4)
        self.assertEqual(store.counts(db_path=self.db)["entity_alias"], 1)

    def test_entity_index_carries_keys_aliases_and_vectors(self):
        atlas = entity(EntityKind.PROJECT, "atlas", "Atlas", aliases=["Bastion rollout"])
        store.upsert_entities([atlas], db_path=self.db)
        store.upsert_entity_vectors([(atlas.entity_id, vec(1, 0))], db_path=self.db)
        index = store.load_entity_index(db_path=self.db)
        self.assertEqual(index.by_key[(EntityKind.PROJECT, "atlas")], atlas.entity_id)
        self.assertEqual(
            index.by_alias[(EntityKind.PROJECT, "bastion dock rollout")]
            if (EntityKind.PROJECT, "bastion dock rollout") in index.by_alias
            else index.by_alias[(EntityKind.PROJECT, "bastion rollout")],
            atlas.entity_id,
        )
        self.assertIn(atlas.entity_id, index.vectors)

    def test_mentions_join_back_to_entities(self):
        atlas = entity(EntityKind.PROJECT, "atlas", "Atlas")
        store.upsert_entities([atlas], db_path=self.db)
        store.upsert_mentions([mention(atlas.entity_id, "e1")], db_path=self.db)
        found = store.entities_for_email("e1", db_path=self.db)
        self.assertEqual([e.entity_id for e in found], [atlas.entity_id])

    def test_re_extracting_replaces_that_emails_mentions(self):
        """A better prompt can legitimately return fewer mentions; the surplus
        would otherwise keep contributing graph edges."""
        a = entity(EntityKind.CASE, "CS1")
        b = entity(EntityKind.CASE, "CS2")
        store.upsert_entities([a, b], db_path=self.db)
        store.upsert_mentions(
            [mention(a.entity_id, "e1", "CS-1"), mention(b.entity_id, "e1", "CS-2")],
            db_path=self.db,
        )
        store.upsert_mentions([mention(a.entity_id, "e1", "CS-1")], db_path=self.db)
        self.assertEqual(len(store.mentions_for_email("e1", db_path=self.db)), 1)

    def test_mentions_of_other_emails_survive(self):
        a = entity(EntityKind.CASE, "CS1")
        store.upsert_entities([a], db_path=self.db)
        store.upsert_mentions(
            [mention(a.entity_id, "e1"), mention(a.entity_id, "e2")], db_path=self.db
        )
        store.upsert_mentions([mention(a.entity_id, "e1", "other")], db_path=self.db)
        self.assertEqual(len(store.mentions_for_email("e2", db_path=self.db)), 1)

    def test_replace_mention_entities_rewrites_provisional_ids(self):
        a = entity(EntityKind.CASE, "CS1")
        store.upsert_entities([a], db_path=self.db)
        provisional = mention("case:CS1", "e1")
        store.upsert_mentions([provisional], db_path=self.db)
        mention_id = store.all_mentions(db_path=self.db)[0][0]
        store.replace_mention_entities([(mention_id, a.entity_id)], db_path=self.db)
        self.assertEqual(
            store.mentions_for_email("e1", db_path=self.db)[0].entity_id, a.entity_id
        )

    def test_emails_for_entity_is_newest_first(self):
        a = entity(EntityKind.CASE, "CS1")
        store.upsert_entities([a], db_path=self.db)
        self.add_raw("old", received_at="2026-08-01T09:00:00+00:00")
        self.add_raw("new", received_at="2026-08-20T09:00:00+00:00")
        store.upsert_mentions(
            [mention(a.entity_id, "old"), mention(a.entity_id, "new")], db_path=self.db
        )
        self.assertEqual(store.emails_for_entity(a.entity_id, db_path=self.db), ["new", "old"])

    def test_email_counts_are_distinct_emails_not_mentions(self):
        a = entity(EntityKind.CASE, "CS1")
        store.upsert_entities([a], db_path=self.db)
        store.upsert_mentions(
            [
                mention(a.entity_id, "e1", "CS-1", chunk_id="e1:0"),
                mention(a.entity_id, "e1", "CS-1", chunk_id="e1:1"),
                mention(a.entity_id, "e2", "CS-1"),
            ],
            db_path=self.db,
        )
        self.assertEqual(
            store.email_counts_for_entities(db_path=self.db)[a.entity_id], 2
        )


class NeighborsTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.person = entity(EntityKind.PERSON, "a@b.com", "Anna")
        self.case = entity(EntityKind.CASE, "CS1", "CS-1")
        self.project = entity(EntityKind.PROJECT, "atlas", "Atlas")
        self.far = entity(EntityKind.TOPIC, "logistics", "logistics")
        store.upsert_entities(
            [self.person, self.case, self.project, self.far], db_path=self.db
        )
        store.upsert_relations(
            [
                Relation(self.person.entity_id, self.case.entity_id,
                         RelationKind.PARTICIPANT_IN, 2.0, ["e1"]),
                Relation(self.case.entity_id, self.project.entity_id,
                         RelationKind.BELONGS_TO, 3.0, ["e1", "e2"]),
                Relation(self.project.entity_id, self.far.entity_id,
                         RelationKind.MENTIONS, 0.5, ["e3"]),
            ],
            db_path=self.db,
        )

    def test_one_hop(self):
        found = store.neighbors(self.case.entity_id, hops=1, db_path=self.db)
        self.assertEqual(
            {e.entity_id for e, _, _ in found},
            {self.person.entity_id, self.project.entity_id},
        )
        self.assertTrue(all(hop == 1 for _, _, hop in found))

    def test_walk_follows_edges_in_both_directions(self):
        """The graph is a DAG only in intent; a one-way walk misses half the
        correlation."""
        found = store.neighbors(self.person.entity_id, hops=2, db_path=self.db)
        self.assertIn(self.project.entity_id, {e.entity_id for e, _, _ in found})

    def test_hop_limit_is_respected(self):
        found = store.neighbors(self.person.entity_id, hops=1, db_path=self.db)
        self.assertNotIn(self.project.entity_id, {e.entity_id for e, _, _ in found})

    def test_weight_accumulates_multiplicatively(self):
        found = dict(
            (e.entity_id, weight)
            for e, weight, _ in store.neighbors(self.person.entity_id, hops=2, db_path=self.db)
        )
        self.assertAlmostEqual(found[self.case.entity_id], 2.0)
        self.assertAlmostEqual(found[self.project.entity_id], 6.0)

    def test_nearest_hop_first(self):
        found = store.neighbors(self.person.entity_id, hops=3, db_path=self.db)
        self.assertEqual([hop for _, _, hop in found], sorted(hop for _, _, hop in found))

    def test_the_anchor_is_never_its_own_neighbor(self):
        found = store.neighbors(self.case.entity_id, hops=3, db_path=self.db)
        self.assertNotIn(self.case.entity_id, {e.entity_id for e, _, _ in found})

    def test_isolated_entity(self):
        lonely = entity(EntityKind.TOPIC, "nothing")
        store.upsert_entities([lonely], db_path=self.db)
        self.assertEqual(store.neighbors(lonely.entity_id, hops=3, db_path=self.db), [])


class CoverageTest(StoreTestCase):
    def test_coverage_reports_each_stage_independently(self):
        store.upsert_chunks([chunk("e1:0"), chunk("e2:0", email_id="e2")], db_path=self.db)
        store.upsert_vectors([("e1:0", to_blob([1, 0]))], db_path=self.db)
        store.upsert_mentions([mention("case:CS1", "e2")], db_path=self.db)
        coverage = store.context_coverage(db_path=self.db)
        self.assertEqual(coverage["chunk"], {"e1", "e2"})
        self.assertEqual(coverage["embed"], {"e1"})
        self.assertEqual(coverage["extract"], {"e2"})

    def test_coverage_feeds_the_incremental_planner(self):
        from pipeline.incremental import context_stages_for

        store.upsert_chunks([chunk("e1:0")], db_path=self.db)
        coverage = store.context_coverage(db_path=self.db)
        self.assertEqual(context_stages_for("e1", coverage), ("embed", "extract"))
        self.assertEqual(
            context_stages_for("e9", coverage), ("chunk", "embed", "extract")
        )


class BriefTest(StoreTestCase):
    def test_mark_dirty_then_read_the_queue(self):
        dirtied = store.mark_briefs_dirty(
            [(BriefNodeType.CASE, "c1", ["e1", "e2"], "hash1")], db_path=self.db
        )
        self.assertEqual(dirtied, 1)
        queue = store.dirty_briefs(db_path=self.db)
        self.assertEqual([b.node_id for b in queue], ["c1"])
        self.assertEqual(queue[0].evidence_email_ids, ["e1", "e2"])

    def test_unchanged_hash_is_not_re_dirtied(self):
        """This skip is the whole cost control: a no-op run must generate no
        briefs at all."""
        store.mark_briefs_dirty(
            [(BriefNodeType.CASE, "c1", ["e1"], "hash1")], db_path=self.db
        )
        store.upsert_briefs(
            [Brief(BriefNodeType.CASE, "c1", "Headline", "body",
                   evidence_email_ids=["e1"], evidence_hash="hash1",
                   generated_at=datetime(2026, 8, 20, tzinfo=timezone.utc))],
            db_path=self.db,
        )
        again = store.mark_briefs_dirty(
            [(BriefNodeType.CASE, "c1", ["e1"], "hash1")], db_path=self.db
        )
        self.assertEqual(again, 0)
        self.assertEqual(store.dirty_briefs(db_path=self.db), [])
        self.assertEqual(
            store.get_brief(BriefNodeType.CASE, "c1", db_path=self.db).headline,
            "Headline",
        )

    def test_changed_hash_clears_the_stale_content(self):
        store.upsert_briefs(
            [Brief(BriefNodeType.CASE, "c1", "Old headline", "old body",
                   evidence_email_ids=["e1"], evidence_hash="hash1")],
            db_path=self.db,
        )
        self.assertEqual(
            store.mark_briefs_dirty(
                [(BriefNodeType.CASE, "c1", ["e1", "e2"], "hash2")], db_path=self.db
            ),
            1,
        )
        brief = store.get_brief(BriefNodeType.CASE, "c1", db_path=self.db)
        self.assertEqual(brief.headline, "")
        self.assertEqual(brief.evidence_email_ids, ["e1", "e2"])
        self.assertEqual(brief.evidence_hash, "hash2")

    def test_node_type_and_id_together_are_the_key(self):
        """node_id is a thread_id for THREAD briefs and an entity_id
        otherwise, so the same string can legitimately name two nodes."""
        store.upsert_briefs(
            [
                Brief(BriefNodeType.THREAD, "x", "thread brief", ""),
                Brief(BriefNodeType.CASE, "x", "case brief", ""),
            ],
            db_path=self.db,
        )
        self.assertEqual(
            store.get_brief(BriefNodeType.THREAD, "x", db_path=self.db).headline,
            "thread brief",
        )
        self.assertEqual(
            store.get_brief(BriefNodeType.CASE, "x", db_path=self.db).headline,
            "case brief",
        )

    def test_missing_brief_is_none(self):
        self.assertIsNone(store.get_brief(BriefNodeType.CASE, "nope", db_path=self.db))


if __name__ == "__main__":
    unittest.main()
