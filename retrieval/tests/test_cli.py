"""PHASES-COMPLEX.md B7."""

from __future__ import annotations

from retrieval.cli import main
from retrieval.tests.fixtures import build_fixture_db


def test_search_prints_ranked_hits(capsys):
    path = build_fixture_db()
    code = main(["search", "Henderson escalation", "--db", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "email-h1a" in out
    assert "bm25" in out


def test_search_with_no_hits(capsys):
    path = build_fixture_db()
    code = main(["search", "completely unrelated gibberish xyzzy", "--db", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "No hits." in out


def test_pack_shows_sections_with_provenance(capsys):
    path = build_fixture_db()
    code = main(["pack", "--email", "email-h1a", "--db", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "This email" in out
    assert "Thread so far" in out
    assert "From " in out


def test_pack_requires_email_or_query(capsys):
    path = build_fixture_db()
    code = main(["pack", "--db", str(path)])
    err = capsys.readouterr().err
    assert code == 1
    assert "Provide --email" in err


def test_brief_prints_stored_brief(capsys):
    path = build_fixture_db()
    code = main(["brief", "case", "ent-case-henderson", "--db", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Henderson escalation" in out
    assert "Send client-facing outage summary" in out


def test_brief_missing_node_reports_failure(capsys):
    path = build_fixture_db()
    code = main(["brief", "case", "does-not-exist", "--db", str(path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "No stored brief" in out
