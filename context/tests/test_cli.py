"""Tests for context/cli.py. Real SQLite, no network, no model.

The CLI is the go/no-go gate for the whole layer, which makes it worth
protecting: a graph view that silently stops rendering projects looks exactly
like a graph that has no projects. `build` is exercised with injected stages so
nothing is embedded or extracted for real.
"""

from __future__ import annotations

import io
import contextlib
import tempfile
import unittest
from pathlib import Path

from context import cli, store
from context.resolve import entity_id_for
from llm.embeddings import to_blob
from models import db
from models.schema import (
    Chunk,
    ChunkKind,
    Entity,
    EntityKind,
    Mention,
    MentionSource,
    Relation,
    RelationKind,
)


def run(*argv):
    """Run the CLI, returning (exit code, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(argv))
    return code, out.getvalue()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "test.db"
        self.db = ["--db", str(self.path)]
        store.init_db(self.path)

    def tearDown(self):
        self._dir.cleanup()

    def seed_graph(self):
        with db.connect(self.path) as conn:
            db.prepare(conn)
            for index in (1, 2):
                conn.execute(
                    "INSERT INTO raw_email (email_id, thread_id, sender, recipients,"
                    " subject, body_text, received_at, read_status, label_ids, headers,"
                    " fetched_at) VALUES (?, 't1', 'Anna <a@stridecore.com>', '[]',"
                    " '[CS-1] Bastion charging', 'body', ?, 'read', '[]', '{}', ?)",
                    ("e{0}".format(index), "2026-08-2{0}T09:00:00+00:00".format(index),
                     "2026-08-2{0}T09:00:00+00:00".format(index)),
                )
            conn.commit()

        project = Entity(
            entity_id=entity_id_for(EntityKind.PROJECT, "bastion"),
            kind=EntityKind.PROJECT, canonical_name="Bastion",
            normalized_key="bastion", mention_count=4, salience=0.8,
        )
        case = Entity(
            entity_id=entity_id_for(EntityKind.CASE, "CS1"), kind=EntityKind.CASE,
            canonical_name="CS-1", normalized_key="CS1", mention_count=2, salience=0.5,
        )
        person = Entity(
            entity_id=entity_id_for(EntityKind.PERSON, "a@stridecore.com"),
            kind=EntityKind.PERSON, canonical_name="Anna",
            normalized_key="a@stridecore.com", mention_count=2, salience=0.4,
        )
        store.upsert_entities([project, case, person], db_path=self.path)
        store.upsert_relations(
            [
                Relation(case.entity_id, project.entity_id, RelationKind.BELONGS_TO,
                         2.0, ["e1", "e2"]),
                Relation(person.entity_id, case.entity_id, RelationKind.PARTICIPANT_IN,
                         1.5, ["e1", "e2"]),
            ],
            db_path=self.path,
        )
        store.upsert_chunks(
            [
                Chunk(chunk_id="e1:0", email_id="e1", ord=0,
                      text="Bastion charging is blocked on CS-1.", kind=ChunkKind.BODY),
                Chunk(chunk_id="e1:1", email_id="e1", ord=1,
                      text="> older quoted history here", kind=ChunkKind.QUOTED),
            ],
            db_path=self.path,
        )
        store.upsert_vectors([("e1:0", to_blob([1, 0, 0]))], db_path=self.path)
        store.upsert_mentions(
            [
                Mention(email_id="e1", entity_id=case.entity_id, span_text="CS-1",
                        source=MentionSource.REGEX),
                Mention(email_id="e1", entity_id=person.entity_id, span_text="Anna",
                        source=MentionSource.HEADER),
                Mention(email_id="e1", entity_id=project.entity_id,
                        span_text="Bastion", chunk_id="e1:0", confidence=0.9,
                        source=MentionSource.LLM),
                Mention(email_id="e2", entity_id=case.entity_id, span_text="CS-1",
                        source=MentionSource.REGEX),
            ],
            db_path=self.path,
        )
        return project, case, person


class GraphTest(CliTestCase):
    def test_empty_graph_says_what_to_run(self):
        code, out = run("graph", *self.db)
        self.assertEqual(code, 1)

    def test_shows_kinds_projects_cases_and_people(self):
        run_project, case, person = self.seed_graph()
        code, out = run("graph", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("ENTITIES BY KIND", out)
        self.assertIn("Bastion", out)
        self.assertIn("CS-1", out)
        self.assertIn("Anna", out)

    def test_reports_the_fragmentation_share(self):
        """A high share of single-email cases means resolution is splitting one
        real case across several nodes — the failure this view exists to catch."""
        self.seed_graph()
        _, out = run("graph", *self.db)
        self.assertIn("FRAGMENTATION CHECK", out)
        self.assertIn("1-EMAIL", out)
        self.assertIn("KEYED ON", out)
        self.assertIn("exact", out, "exact-keyed kinds cannot fragment")
        # Reported per kind: a DOCUMENT appearing once is not fragmentation,
        # a CASE appearing once probably is.
        self.assertIn("case", out)

    def test_a_graph_with_no_projects_says_so_rather_than_printing_nothing(self):
        store.upsert_entities(
            [
                Entity(entity_id="x", kind=EntityKind.CASE, canonical_name="CS-9",
                       normalized_key="CS9")
            ],
            db_path=self.path,
        )
        code, out = run("graph", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("No PROJECT entities", out)


class EntitiesTest(CliTestCase):
    def test_kind_filter(self):
        self.seed_graph()
        _, out = run("entities", "--kind", "case", *self.db)
        self.assertIn("CS-1", out)
        self.assertNotIn("Bastion", out)

    def test_shows_mention_and_email_counts(self):
        self.seed_graph()
        _, out = run("entities", "--kind", "case", *self.db)
        self.assertIn("MENTIONS", out)
        self.assertIn("EMAILS", out)

    def test_empty(self):
        code, out = run("entities", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("No entities", out)


class EmailTest(CliTestCase):
    def test_groups_mentions_by_which_pass_found_them(self):
        """A wrong regex is a different fix from the model inventing something."""
        self.seed_graph()
        code, out = run("email", "e1", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("HEADER", out)
        self.assertIn("REGEX", out)
        self.assertIn("LLM", out)
        self.assertIn("Anna", out)
        self.assertIn("Bastion", out)

    def test_unknown_email(self):
        self.assertEqual(run("email", "nope", *self.db)[0], 1)

    def test_email_with_no_mentions(self):
        self.seed_graph()
        with db.connect(self.path) as conn:
            conn.execute("DELETE FROM mention")
            conn.commit()
        code, out = run("email", "e1", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("No mentions extracted", out)


class ChunksTest(CliTestCase):
    def test_shows_every_chunk_with_its_kind(self):
        self.seed_graph()
        code, out = run("chunks", "e1", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("body", out)
        self.assertIn("quoted", out)
        self.assertIn("Bastion charging is blocked", out)
        self.assertIn("older quoted history", out)

    def test_no_chunks(self):
        self.assertEqual(run("chunks", "e9", *self.db)[0], 1)


class BuildTest(CliTestCase):
    def test_no_raw_emails(self):
        self.assertEqual(run("build", *self.db)[0], 1)

    def test_dry_run_writes_nothing(self):
        self.seed_graph()
        with db.connect(self.path) as conn:
            conn.execute("DELETE FROM chunk")
            conn.commit()
        code, out = run("build", "--dry-run", *self.db)
        self.assertEqual(code, 0)
        self.assertIn("2/2 emails need work", out)
        self.assertEqual(store.counts(db_path=self.path)["chunk"], 0)

    def build_with_fakes(self, *argv):
        """Run `build` with the context stages stubbed — no ollama, no model."""
        from pipeline.orchestrate import Pipeline

        def fakes():
            return {
                "chunk": lambda raw: [
                    Chunk(chunk_id=raw.email_id + ":0", email_id=raw.email_id, ord=0,
                          text="Bastion body", kind=ChunkKind.BODY)
                ],
                "embed": lambda chunks: [(c.chunk_id, to_blob([1, 0])) for c in chunks],
                "extract": lambda raw, chunks: [
                    Mention(email_id=raw.email_id, entity_id="case:CS1",
                            span_text="CS-1", source=MentionSource.REGEX)
                ],
            }

        real = Pipeline._context_defaults
        Pipeline._context_defaults = staticmethod(fakes)
        try:
            return run("build", *(list(argv) + self.db))
        finally:
            Pipeline._context_defaults = real

    def test_build_persists_chunks_vectors_and_mentions(self):
        self.seed_graph()
        with db.connect(self.path) as conn:
            for table in ("chunk", "chunk_vec", "mention", "entity", "relation"):
                conn.execute("DELETE FROM " + table)
            conn.commit()

        code, out = self.build_with_fakes()
        self.assertEqual(code, 0)
        counts = store.counts(db_path=self.path)
        self.assertEqual(counts["chunk"], 2)
        self.assertEqual(counts["chunk_vec"], 2)
        self.assertEqual(counts["mention"], 2)
        self.assertEqual(counts["entity"], 1, "consolidate ran and resolved")
        self.assertIn("Consolidating", out)

    def test_a_second_build_finds_nothing_to_do(self):
        """Context coverage is what makes a no-op re-run free — no embedding
        calls, and no extraction calls."""
        self.seed_graph()
        self.build_with_fakes()
        code, out = self.build_with_fakes()
        self.assertEqual(code, 0)
        self.assertIn("0/2 emails need work", out)

    def test_all_forces_every_email_back_through(self):
        self.seed_graph()
        self.build_with_fakes()
        code, out = self.build_with_fakes("--all")
        self.assertEqual(code, 0)
        self.assertIn("2/2 emails need work", out)


if __name__ == "__main__":
    unittest.main()
