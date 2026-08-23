"""PHASES-COMPLEX.md B3."""

from __future__ import annotations

from retrieval.pack import build_pack
from retrieval.tests.fixtures import build_fixture_db


def test_priority_order_for_a_well_connected_anchor():
    path = build_fixture_db()
    pack = build_pack(anchor_email_id="email-h1a", budget_chars=6000, db_path=path)

    labels = [s.label for s in pack.sections]
    assert labels[0] == "This email"
    assert "Thread so far" in labels  # thread-henderson-name has 2 messages
    assert any(label.startswith("Case:") for label in labels)
    assert any(label.startswith("Project:") for label in labels)
    assert any(label.startswith("Open items") for label in labels)
    assert any(label.startswith("From ") for label in labels)  # foreign chunks


def test_thread_brief_omitted_for_a_single_message_thread():
    path = build_fixture_db()
    pack = build_pack(anchor_email_id="email-h2", budget_chars=6000, db_path=path)
    # thread-henderson-ticket has exactly one message (email-h2).
    assert "Thread so far" not in [s.label for s in pack.sections]


def test_anchor_never_appears_as_a_foreign_section():
    path = build_fixture_db()
    pack = build_pack(anchor_email_id="email-h1a", budget_chars=6000, db_path=path)
    foreign_sources = [
        eid
        for section in pack.sections
        if section.label.startswith("From ")
        for eid in section.source_email_ids
    ]
    assert "email-h1a" not in foreign_sources


def test_foreign_sections_are_deduplicated_by_email():
    path = build_fixture_db()
    pack = build_pack(anchor_email_id="email-h1a", budget_chars=6000, db_path=path)
    foreign_email_ids = [
        eid
        for section in pack.sections
        if section.label.startswith("From ")
        for eid in section.source_email_ids
    ]
    assert len(foreign_email_ids) == len(set(foreign_email_ids))


def test_budget_is_respected():
    path = build_fixture_db()
    pack = build_pack(anchor_email_id="email-h1a", budget_chars=40, db_path=path)
    assert pack.total_chars <= 40
    assert sum(len(s.text) for s in pack.sections) <= 40


def test_priority_order_holds_when_budget_is_tight():
    path = build_fixture_db()
    pack = build_pack(anchor_email_id="email-h1a", budget_chars=40, db_path=path)
    # Only enough room for (a truncated) first-priority section.
    assert len(pack.sections) == 1
    assert pack.sections[0].label == "This email"


def test_query_only_pack_has_no_anchor_sections():
    path = build_fixture_db()
    pack = build_pack(query="Henderson escalation", budget_chars=6000, db_path=path)
    labels = [s.label for s in pack.sections]
    assert "This email" not in labels
    assert "Thread so far" not in labels
