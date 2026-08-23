"""Tests for context/consolidate.py. Real SQLite, no network, no model.

Embedding is injected, so the corpus pass is exercised end to end with no
ollama running. The rules under test are the ones that decide whether the
graph correlates real work or noise: the two-email floor on belongs_to, the
inverse-document-frequency treatment of corpus-wide entities, and the brief
cache key.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from context import store
from context.consolidate import (
    MIN_BELONGS_TO_EVIDENCE,
    MIN_CORPUS_FOR_STOP_ENTITIES,
    STOP_ENTITY_DOC_FRACTION,
    consolidate,
    evidence_hash,
)
from context.resolve import entity_id_for
from context.tests.fakes import vec
from models import db
from models.schema import (
    BriefNodeType,
    ChunkKind,
    Chunk,
    EntityKind,
    Mention,
    MentionSource,
    RelationKind,
)


def mention(provisional, email_id, span, source=MentionSource.REGEX, confidence=1.0):
    return Mention(
        email_id=email_id,
        entity_id=provisional,
        span_text=span,
        confidence=confidence,
        source=source,
    )


class ConsolidateTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = Path(self._dir.name) / "test.db"
        store.init_db(self.db)

    def tearDown(self):
        self._dir.cleanup()

    def add_email(self, email_id, thread_id="t1", processed_at="2026-08-20T10:00:00+00:00"):
        with db.connect(self.db) as conn:
            db.prepare(conn)
            conn.execute(
                "INSERT OR REPLACE INTO raw_email (email_id, thread_id, sender,"
                " recipients, subject, received_at, read_status, label_ids, headers,"
                " fetched_at) VALUES (?,?,'a@b.com','[]','s',"
                "'2026-08-20T09:00:00+00:00','read','[]','{}','2026-08-20T09:00:00+00:00')",
                (email_id, thread_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO processed_email (email_id, thread_id, sender,"
                " subject, received_at, read_status, processed_at)"
                " VALUES (?,?,'a@b.com','s','2026-08-20T09:00:00+00:00','read',?)",
                (email_id, thread_id, processed_at),
            )
            conn.commit()

    def seed(self, mentions, emails=None):
        for email_id in emails or sorted({m.email_id for m in mentions}):
            self.add_email(email_id)
        store.upsert_mentions(mentions, db_path=self.db)

    def no_embed(self, texts):
        return [b"" for _ in texts]


class ResolutionTest(ConsolidateTestCase):
    def test_provisional_ids_become_real_entities(self):
        self.seed(
            [
                mention("case:CS40350", "e1", "CS-40350"),
                mention("case:CS40350", "e2", "CS-40350"),
                mention("person:a@stridecore.com", "e1", "Anna"),
            ]
        )
        stats = consolidate(self.db, embed=self.no_embed)
        self.assertEqual(stats.mentions_total, 3)
        self.assertEqual(stats.mentions_resolved, 3)
        self.assertEqual(stats.entities_created, 2)
        expected = entity_id_for(EntityKind.CASE, "CS40350")
        self.assertEqual(
            {m.entity_id for m in store.mentions_for_email("e1", db_path=self.db)},
            {expected, entity_id_for(EntityKind.PERSON, "a@stridecore.com")},
        )

    def test_is_idempotent(self):
        self.seed([mention("case:CS1", "e1", "CS-1"), mention("case:CS1", "e2", "CS-1")])
        first = consolidate(self.db, embed=self.no_embed)
        second = consolidate(self.db, embed=self.no_embed)
        self.assertEqual(first.entities_created, 1)
        self.assertEqual(second.entities_created, 0)
        self.assertEqual(second.mentions_resolved, 0)
        self.assertEqual(store.counts(db_path=self.db)["entity"], 1)

    def test_empty_corpus_is_a_no_op(self):
        stats = consolidate(self.db, embed=self.no_embed)
        self.assertEqual(stats.mentions_total, 0)
        self.assertEqual(store.counts(db_path=self.db)["entity"], 0)

    def test_entity_vectors_are_persisted_so_rung_three_can_fire_later(self):
        """Without this the embeddings are computed and thrown away, and every
        name entity looks brand new forever — the ladder silently loses a rung."""
        self.seed([mention("project:atlas", "e1", "Atlas")])
        consolidate(self.db, embed=lambda texts: [vec(1, 0, 0, 0) for _ in texts])
        index = store.load_entity_index(db_path=self.db)
        self.assertIn(entity_id_for(EntityKind.PROJECT, "atlas"), index.vectors)

    def test_a_later_near_duplicate_merges_via_the_stored_vector(self):
        self.seed([mention("project:henderson escalation", "e1", "Henderson escalation")])
        consolidate(self.db, embed=lambda texts: [vec(1, 0, 0, 0) for _ in texts])
        store.upsert_mentions(
            [mention("project:henderson issue", "e2", "Henderson issue")], db_path=self.db
        )
        self.add_email("e2")
        stats = consolidate(self.db, embed=lambda texts: [vec(1, 0.2, 0, 0) for _ in texts])
        self.assertEqual(stats.entities_merged, 1)
        self.assertEqual(store.counts(db_path=self.db)["entity"], 1)

    def test_unavailable_embeddings_degrade_to_deterministic(self):
        """A missing embedding model must not cost the corpus its graph."""
        def explode(texts):
            raise RuntimeError("ollama is down")

        self.seed([mention("project:atlas", "e1", "Atlas")])
        stats = consolidate(self.db, embed=explode)
        self.assertEqual(stats.entities_created, 1)
        self.assertEqual(stats.by_rung.get("vector", 0), 0)

    def test_salience_favours_spread_over_repetition(self):
        """A node named forty times in one email is less central than one that
        turns up in ten separate conversations."""
        loud = [mention("topic:loud", "e1", "loud", confidence=1.0) for _ in range(1)]
        spread = [mention("topic:spread", "e{0}".format(i), "spread") for i in range(1, 8)]
        # Same entity, many chunks of one email, to inflate mention_count only.
        many = [
            Mention(email_id="e1", entity_id="topic:loud", span_text="loud",
                    chunk_id="e1:{0}".format(i), source=MentionSource.LLM)
            for i in range(20)
        ]
        self.seed(loud + many + spread)
        consolidate(self.db, embed=self.no_embed)
        by_key = {e.normalized_key: e for e in store.all_entities(db_path=self.db)}
        self.assertGreater(by_key["spread"].salience, by_key["loud"].salience)


class RelationTest(ConsolidateTestCase):
    def test_person_participates_in_a_case(self):
        self.seed(
            [
                mention("person:a@x.com", "e1", "Anna"),
                mention("case:CS1", "e1", "CS-1"),
            ]
        )
        consolidate(self.db, embed=self.no_embed)
        person = entity_id_for(EntityKind.PERSON, "a@x.com")
        rels = store.relations_for_entity(person, db_path=self.db)
        self.assertIn(
            RelationKind.PARTICIPANT_IN, {r.rel for r in rels}
        )

    def test_belongs_to_needs_two_emails(self):
        """One shared email is a coincidence — a "see also", a digest, a cc'd
        summary — and an edge built on it makes the graph correlate noise."""
        self.assertEqual(MIN_BELONGS_TO_EVIDENCE, 2)
        self.seed(
            [
                mention("case:CS1", "e1", "CS-1"),
                mention("project:atlas", "e1", "Atlas"),
            ]
        )
        consolidate(self.db, embed=self.no_embed)
        case = entity_id_for(EntityKind.CASE, "CS1")
        self.assertNotIn(
            RelationKind.BELONGS_TO,
            {r.rel for r in store.relations_for_entity(case, db_path=self.db)},
        )

    def test_belongs_to_appears_on_the_second_email(self):
        self.seed(
            [
                mention("case:CS1", "e1", "CS-1"),
                mention("project:atlas", "e1", "Atlas"),
                mention("case:CS1", "e2", "CS-1"),
                mention("project:atlas", "e2", "Atlas"),
            ]
        )
        consolidate(self.db, embed=self.no_embed)
        case = entity_id_for(EntityKind.CASE, "CS1")
        edge = next(
            r
            for r in store.relations_for_entity(case, db_path=self.db)
            if r.rel == RelationKind.BELONGS_TO
        )
        self.assertEqual(edge.evidence_email_ids, ["e1", "e2"])

    def test_edges_carry_their_evidence(self):
        self.seed(
            [
                mention("person:a@x.com", "e1", "Anna"),
                mention("case:CS1", "e1", "CS-1"),
                mention("person:a@x.com", "e2", "Anna"),
                mention("case:CS1", "e2", "CS-1"),
            ]
        )
        consolidate(self.db, embed=self.no_embed)
        edge = next(
            r
            for r in store.relations_for_entity(
                entity_id_for(EntityKind.CASE, "CS1"), db_path=self.db
            )
            if r.rel == RelationKind.PARTICIPANT_IN
        )
        self.assertEqual(edge.evidence_email_ids, ["e1", "e2"])

    def test_incidental_mentions_make_weaker_edges(self):
        strong = [
            mention("person:a@x.com", "e1", "Anna"),
            mention("case:CS1", "e1", "CS-1", confidence=1.0),
        ]
        weak = [
            mention("person:a@x.com", "e2", "Anna"),
            mention("case:CS2", "e2", "CS-2", confidence=0.4),
        ]
        self.seed(strong + weak)
        consolidate(self.db, embed=self.no_embed)
        person = entity_id_for(EntityKind.PERSON, "a@x.com")
        weights = {
            r.dst_entity_id: r.weight
            for r in store.relations_for_entity(person, db_path=self.db)
            if r.rel == RelationKind.PARTICIPANT_IN
        }
        self.assertGreater(
            weights[entity_id_for(EntityKind.CASE, "CS1")],
            weights[entity_id_for(EntityKind.CASE, "CS2")],
        )

    def _wide_corpus(self, emails=40):
        """A corpus where "relay" is in every email and each case in one."""
        mentions = []
        for index in range(emails):
            email_id = "e{0}".format(index)
            mentions.append(mention("person:relay@x.com", email_id, "Relay"))
            mentions.append(mention("case:CS{0}".format(index), email_id, "CS-x"))
        return mentions

    def test_corpus_wide_entities_get_no_edges_of_any_type(self):
        """The mailbox owner and the relay address are in nearly every message.
        Damping their weight is not enough — the walk still traverses the edge,
        so they act as a junction connecting every case to every project at two
        hops and the graph channel answers "related to everything"."""
        self.assertEqual(STOP_ENTITY_DOC_FRACTION, 0.5)
        self.seed(self._wide_corpus())
        consolidate(self.db, embed=self.no_embed)
        relay = entity_id_for(EntityKind.PERSON, "relay@x.com")
        self.assertEqual(store.relations_for_entity(relay, db_path=self.db), [])

    def test_no_two_hop_path_between_cases_through_the_relay(self):
        self.seed(self._wide_corpus())
        consolidate(self.db, embed=self.no_embed)
        first = entity_id_for(EntityKind.CASE, "CS0")
        reachable = {
            entity.entity_id
            for entity, _, _ in store.neighbors(first, hops=2, db_path=self.db)
        }
        self.assertNotIn(entity_id_for(EntityKind.CASE, "CS1"), reachable)

    def test_the_rule_waits_for_enough_corpus(self):
        """In a five-email mailbox every entity is in more than half of it.
        Applying the rule there yields a graph with no edges at all."""
        self.assertEqual(MIN_CORPUS_FOR_STOP_ENTITIES, 20)
        self.seed(
            [
                mention("person:a@x.com", "e1", "Anna"),
                mention("case:CS1", "e1", "CS-1"),
                mention("person:a@x.com", "e2", "Anna"),
                mention("case:CS1", "e2", "CS-1"),
            ]
        )
        consolidate(self.db, embed=self.no_embed)
        self.assertTrue(
            store.relations_for_entity(
                entity_id_for(EntityKind.PERSON, "a@x.com"), db_path=self.db
            )
        )

    def test_rare_pairs_still_get_their_untyped_edge(self):
        mentions = self._wide_corpus(30)
        mentions += [
            mention("topic:rare_a", "e0", "rare a"),
            mention("topic:rare_b", "e0", "rare b"),
        ]
        self.seed(mentions)
        consolidate(self.db, embed=self.no_embed)
        rare = entity_id_for(EntityKind.TOPIC, "rare_a")
        rels = store.relations_for_entity(rare, db_path=self.db)
        self.assertIn(
            entity_id_for(EntityKind.TOPIC, "rare_b"),
            {r.src_entity_id for r in rels} | {r.dst_entity_id for r in rels},
        )


class BriefDirtyingTest(ConsolidateTestCase):
    def test_threads_cases_projects_and_people_all_get_a_node(self):
        self.seed(
            [
                mention("case:CS1", "e1", "CS-1"),
                mention("project:atlas", "e1", "Atlas"),
                mention("person:a@x.com", "e1", "Anna"),
                mention("topic:logistics", "e1", "logistics"),
            ]
        )
        consolidate(self.db, embed=self.no_embed)
        node_types = {b.node_type for b in store.dirty_briefs(db_path=self.db)}
        self.assertEqual(
            node_types,
            {BriefNodeType.THREAD, BriefNodeType.CASE,
             BriefNodeType.PROJECT, BriefNodeType.PERSON},
        )

    def test_topics_and_orgs_get_no_brief(self):
        self.seed([mention("topic:logistics", "e1", "logistics")])
        consolidate(self.db, embed=self.no_embed)
        ids = {b.node_id for b in store.dirty_briefs(db_path=self.db)}
        self.assertNotIn(entity_id_for(EntityKind.TOPIC, "logistics"), ids)

    def test_a_second_run_dirties_nothing_new(self):
        """A stale hash would mean briefs never refresh; an over-eager one
        means paying for every brief on every run."""
        self.seed([mention("case:CS1", "e1", "CS-1")])
        first = consolidate(self.db, embed=self.no_embed)
        second = consolidate(self.db, embed=self.no_embed)
        self.assertGreater(first.briefs_dirtied, 0)
        self.assertEqual(second.briefs_dirtied, 0)

    def test_a_new_email_on_a_case_re_dirties_it(self):
        self.seed([mention("case:CS1", "e1", "CS-1")])
        consolidate(self.db, embed=self.no_embed)
        self.add_email("e2")
        store.upsert_mentions([mention("case:CS1", "e2", "CS-1")], db_path=self.db)
        self.assertGreater(consolidate(self.db, embed=self.no_embed).briefs_dirtied, 0)

    def test_reprocessing_an_email_re_dirties_its_brief(self):
        """The email set did not change, but its summary did — that is new
        evidence for the brief above it."""
        self.seed([mention("case:CS1", "e1", "CS-1")])
        consolidate(self.db, embed=self.no_embed)
        self.add_email("e1", processed_at="2026-08-21T11:00:00+00:00")
        self.assertGreater(consolidate(self.db, embed=self.no_embed).briefs_dirtied, 0)


class EvidenceHashTest(unittest.TestCase):
    def test_order_does_not_matter(self):
        self.assertEqual(
            evidence_hash([("a", "t1"), ("b", "t2")]),
            evidence_hash([("b", "t2"), ("a", "t1")]),
        )

    def test_a_new_email_changes_it(self):
        self.assertNotEqual(
            evidence_hash([("a", "t1")]), evidence_hash([("a", "t1"), ("b", "t2")])
        )

    def test_a_reprocessed_email_changes_it(self):
        self.assertNotEqual(evidence_hash([("a", "t1")]), evidence_hash([("a", "t2")]))

    def test_a_missing_timestamp_is_stable(self):
        self.assertEqual(evidence_hash([("a", None)]), evidence_hash([("a", None)]))

    def test_empty_evidence(self):
        self.assertTrue(evidence_hash([]))


if __name__ == "__main__":
    unittest.main()
