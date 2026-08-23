"""Sanity checks on the B1 fixture itself — mostly a guard against future
edits to fixtures.py silently breaking the scenario every other retrieval
test depends on."""

from __future__ import annotations

from models import db

from .fixtures import build_fixture_db


def _bm25_hits(conn, query: str):
    # Double-quoted as an FTS5 phrase so punctuation in the query (e.g. the
    # hyphen in "CASE-4471") is matched literally rather than parsed as FTS5
    # query syntax.
    rows = conn.execute(
        "SELECT chunk.email_id FROM chunk_fts JOIN chunk ON chunk.rowid = chunk_fts.rowid "
        "WHERE chunk_fts MATCH ?",
        ('"{0}"'.format(query),),
    ).fetchall()
    return {row["email_id"] for row in rows}


def test_table_counts():
    path = build_fixture_db()
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_email").fetchone()[0] == 12
        assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 12
        kinds = {
            row["kind"]: row["n"]
            for row in conn.execute(
                "SELECT kind, COUNT(*) AS n FROM entity GROUP BY kind"
            ).fetchall()
        }
        assert kinds == {"person": 5, "org": 2, "case": 3, "project": 2}


def test_same_case_no_shared_vocabulary_scenario():
    """The scenario every retrieval test builds on: three emails in the same
    case, three different threads, almost no shared vocabulary."""
    path = build_fixture_db()
    with db.connect(path) as conn:
        # email-h3 names neither "Henderson" nor "CASE-4471" anywhere in its text.
        assert "email-h3" not in _bm25_hits(conn, "Henderson")
        assert "email-h3" not in _bm25_hits(conn, "CASE-4471")

        # email-h2 never says "Henderson" either — only the ticket ID.
        assert "email-h2" not in _bm25_hits(conn, "Henderson")
        assert "email-h2" in _bm25_hits(conn, "CASE-4471")

        # All three are in different threads.
        threads = {
            row["thread_id"]
            for row in conn.execute(
                "SELECT DISTINCT thread_id FROM raw_email "
                "WHERE email_id IN ('email-h1a', 'email-h2', 'email-h3')"
            ).fetchall()
        }
        assert len(threads) == 3

        # h3 is reachable only via the entity graph: Alex/Jordan participate
        # in the Henderson case, and that relation's evidence includes h3.
        row = conn.execute(
            "SELECT evidence_email_ids FROM relation "
            "WHERE src_entity_id = 'ent-alex' AND dst_entity_id = 'ent-case-henderson'"
        ).fetchone()
        assert "email-h3" in row["evidence_email_ids"]

        # h3 has no case/project mention at all — graph-only findable.
        case_mentions = conn.execute(
            "SELECT m.entity_id FROM mention m JOIN entity e ON e.entity_id = m.entity_id "
            "WHERE m.email_id = 'email-h3' AND e.kind IN ('case', 'project')"
        ).fetchall()
        assert case_mentions == []


def test_alias_resolves_to_same_entity_as_canonical_name():
    path = build_fixture_db()
    with db.connect(path) as conn:
        row = conn.execute(
            "SELECT entity_id FROM entity_alias WHERE normalized_alias = 'case-4471'"
        ).fetchone()
        assert row["entity_id"] == "ent-case-henderson"


def test_each_call_returns_an_independently_readable_db():
    """A fresh temp-file DB per call — not a shared ':memory:' path that
    would look empty to the next models.db.connect() caller."""
    first = build_fixture_db()
    second = build_fixture_db()
    assert first != second
    with db.connect(first) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_email").fetchone()[0] == 12
