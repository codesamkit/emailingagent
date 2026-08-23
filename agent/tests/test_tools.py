"""agent/tools.py: dispatch against a fake DB, no network, no model calls
(except summarize_selection, which injects a fake Anthropic-shaped client)."""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone

import pytest

from agent import tools
from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus, ReplyOutlineStatus
from pipeline import persist

UTC = timezone.utc
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _email(email_id="e1", **overrides) -> ProcessedEmail:
    defaults = dict(
        email_id=email_id, thread_id="t1", sender="alex@example.com", subject="Hi",
        received_at=NOW, read_status=ReadStatus.READ,
        is_no_reply=False, importance_score=70.0, importance_level=ImportanceLevel.HIGH,
        summary="A short summary.", category="team planning",
        reply_outline=["Acknowledge", "Confirm Friday"],
        reply_outline_status=ReplyOutlineStatus.SUGGESTED,
        processed_at=NOW,
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    persist.upsert([_email("e1"), _email("e2", sender="dana@example.com", subject="Other")], path)
    return path


class TestDispatchRouting:
    def test_unknown_tool_returns_error_not_raise(self, db):
        result = tools.dispatch("not_a_real_tool", {}, db_path=db)
        assert "error" in result

    def test_missing_required_arg_returns_error_not_raise(self, db):
        result = tools.dispatch("get_email", {}, db_path=db)
        assert "error" in result

    def test_tool_specs_names_match_dispatch_handlers(self):
        spec_names = {spec["name"] for spec in tools.TOOL_SPECS}
        assert spec_names == set(tools._HANDLERS)


class TestGetEmail:
    def test_returns_known_email(self, db):
        result = tools.dispatch("get_email", {"email_id": "e1"}, db_path=db)
        assert result["emailId"] == "e1"
        assert result["subject"] == "Hi"
        assert "error" not in result

    def test_unknown_email_is_a_tool_error(self, db):
        result = tools.dispatch("get_email", {"email_id": "nope"}, db_path=db)
        assert "error" in result

    def test_body_is_truncated(self, db, monkeypatch):
        from ingestion import store as raw_store

        long_body = "x" * (tools.MAX_BODY_CHARS + 500)
        monkeypatch.setattr(
            raw_store, "get",
            lambda email_id, db_path=None: type("R", (), {"body": long_body})(),
        )
        result = tools.dispatch("get_email", {"email_id": "e1"}, db_path=db)
        assert len(result["body"]) <= tools.MAX_BODY_CHARS + 1  # +1 for the ellipsis char


class TestListQueue:
    def test_lists_seeded_emails(self, db):
        result = tools.dispatch("list_queue", {}, db_path=db)
        assert result["total"] == 2
        assert {e["email_id"] for e in result["emails"]} == {"e1", "e2"}

    def test_filters_apply(self, db):
        result = tools.dispatch("list_queue", {"search": "Other"}, db_path=db)
        assert result["total"] == 1
        assert result["emails"][0]["email_id"] == "e2"

    def test_limit_is_capped(self, db):
        result = tools.dispatch("list_queue", {"limit": 9999}, db_path=db)
        assert len(result["emails"]) <= tools.MAX_QUEUE_ITEMS

    def test_items_are_lean_not_full_email_shape(self, db):
        result = tools.dispatch("list_queue", {}, db_path=db)
        item = result["emails"][0]
        assert "summary" not in item
        assert "calendarContext" not in item


class TestDraftReply:
    def test_expands_outline(self, db, monkeypatch):
        import drafting.expand as expand_module

        monkeypatch.setattr(
            expand_module, "expand_outline_to_full_draft",
            lambda email_id, outline=None, client=None: "Hi Alex,\n\nSounds good.\n\nBest,",
        )
        # tools._draft_reply imports expand_outline_to_full_draft locally each
        # call, so patching the module attribute above is what takes effect.
        result = tools.dispatch("draft_reply", {"email_id": "e1"}, db_path=db)
        assert result["draft"].startswith("Hi Alex,")

    def test_email_without_outline_is_a_tool_error(self, db):
        persist.upsert([_email("e3", reply_outline=None, reply_outline_status=ReplyOutlineStatus.NONE)], db)
        result = tools.dispatch("draft_reply", {"email_id": "e3"}, db_path=db)
        assert "error" in result

    def test_unknown_email_is_a_tool_error(self, db):
        result = tools.dispatch("draft_reply", {"email_id": "nope"}, db_path=db)
        assert "error" in result

    def test_instructions_are_folded_in_as_a_bullet(self, db, monkeypatch):
        import drafting.expand as expand_module

        captured = {}

        def fake_expand(email_id, outline=None, client=None):
            captured["outline"] = outline
            return "draft text is long enough to pass any floor check here"

        monkeypatch.setattr(expand_module, "expand_outline_to_full_draft", fake_expand)
        tools.dispatch(
            "draft_reply", {"email_id": "e1", "instructions": "mention Friday"}, db_path=db
        )
        assert any("mention Friday" in bullet for bullet in captured["outline"])


class TestSummarizeSelection:
    def test_summarizes_found_emails_with_a_fake_client(self, db, monkeypatch):
        import agent.tools as tools_module

        class _Block:
            type = "text"
            text = _json.dumps({"reason": "both about planning", "summary": "x" * 60})

        class _Fake:
            class messages:
                @staticmethod
                def create(**kwargs):
                    return type("R", (), {"content": [_Block()]})()

        monkeypatch.setattr(tools_module, "dispatch", tools_module.dispatch)  # sanity no-op
        import llm.client as client_module

        monkeypatch.setattr(client_module, "get_client", lambda stage=None, provider=None: _Fake())
        result = tools.dispatch("summarize_selection", {"email_ids": ["e1", "e2"]}, db_path=db)
        assert set(result["email_ids"]) == {"e1", "e2"}
        assert len(result["summary"]) >= 50

    def test_no_matching_emails_is_a_tool_error(self, db, monkeypatch):
        result = tools.dispatch("summarize_selection", {"email_ids": ["nope"]}, db_path=db)
        assert "error" in result

    def test_id_list_is_capped(self, db, monkeypatch):
        import llm.client as client_module

        class _Block:
            type = "text"
            text = _json.dumps({"reason": "ok", "summary": "x" * 60})

        class _Fake:
            class messages:
                @staticmethod
                def create(**kwargs):
                    return type("R", (), {"content": [_Block()]})()

        monkeypatch.setattr(client_module, "get_client", lambda stage=None, provider=None: _Fake())
        many_ids = ["e1"] * (tools.MAX_SUMMARIZE_SELECTION_IDS + 10)
        result = tools.dispatch("summarize_selection", {"email_ids": many_ids}, db_path=db)
        assert "error" not in result


@pytest.fixture
def graph_db(db):
    """`db` plus a small real context graph — two entities and their briefs,
    written through context.store the same way the pipeline writes them. The
    retrieval-backed tools read the real tables now, so the fixture has to be
    real rows rather than agent/fixtures.py's hand-written objects."""
    from context import store as context_store
    from models.schema import Brief, Entity, EntityKind

    context_store.upsert_entities(
        [
            Entity(
                entity_id="ent-henderson", kind=EntityKind.CASE,
                canonical_name="Henderson escalation", normalized_key="hend-4471",
                aliases=["HEND-4471"], mention_count=4, salience=0.65,
                first_seen=NOW, last_seen=NOW,
            ),
            Entity(
                entity_id="ent-priya", kind=EntityKind.PERSON,
                canonical_name="Priya Shah", normalized_key="priya@example.com",
                mention_count=6, salience=0.8, first_seen=NOW, last_seen=NOW,
            ),
        ],
        db_path=db,
    )
    context_store.upsert_briefs(
        [
            Brief(
                node_type="case", node_id="ent-henderson",
                headline="Henderson escalation still open",
                body_md="Support has not confirmed a root cause.",
                open_items=["Confirm root cause with support"],
                evidence_email_ids=["e1"], generated_at=NOW,
            ),
            Brief(
                node_type="person", node_id="ent-priya",
                headline="Priya is waiting on an ETA",
                body_md="Flagged the escalation on 2026-08-20.",
                open_items=["Reply to Priya with an ETA"],
                evidence_email_ids=["e1"], generated_at=NOW,
            ),
            Brief(
                node_type="thread", node_id="t1",
                headline="Scheduling thread",
                body_md="Confirming Friday.", open_items=[],
                evidence_email_ids=["e1"], generated_at=NOW,
            ),
        ],
        db_path=db,
    )
    return db


class TestRetrievalTools:
    """search_context / get_thread_brief / get_entity_brief / list_entities /
    find_open_items read the real context graph (Track A's context.store and
    Track B's retrieval.*), not fixtures."""

    def test_search_context_returns_bounded_sections(self, graph_db, monkeypatch):
        # No live embedding backend in a unit test -- the vector channel is
        # stubbed out the way retrieval.search documents (returns None ->
        # BM25 and the graph walk carry the query).
        from retrieval import search as search_module

        monkeypatch.setattr(search_module, "_embed_query", lambda query: None)
        result = tools.dispatch("search_context", {"query": "henderson"}, db_path=graph_db)
        assert "sections" in result
        assert len(result["sections"]) <= tools.MAX_LIST_ITEMS

    def test_search_context_survives_a_dead_embedding_backend(self, graph_db, monkeypatch):
        """An unreachable ollama used to raise out of search() and fail the
        whole tool, taking BM25 and the graph walk with it."""
        import llm.embeddings as embeddings_module

        def _boom(texts):
            raise RuntimeError("Could not reach Ollama at http://localhost:11434")

        # Patched at the backend, not at _embed_query -- the guard under test
        # lives inside _embed_query, so replacing it would skip the very code
        # this asserts on.
        monkeypatch.setattr(embeddings_module, "embed_texts", _boom)
        result = tools.dispatch("search_context", {"query": "henderson"}, db_path=graph_db)
        assert "error" not in result
        assert "sections" in result

    def test_get_thread_brief_known_and_unknown(self, graph_db):
        found = tools.dispatch("get_thread_brief", {"thread_id": "t1"}, db_path=graph_db)
        assert "error" not in found
        assert found["headline"] == "Scheduling thread"
        missing = tools.dispatch("get_thread_brief", {"thread_id": "no-such-thread"}, db_path=graph_db)
        assert "error" in missing

    def test_get_entity_brief_known_and_unknown(self, graph_db):
        found = tools.dispatch("get_entity_brief", {"entity_id": "ent-henderson"}, db_path=graph_db)
        assert "error" not in found
        assert found["node_type"] == "case"
        missing = tools.dispatch("get_entity_brief", {"entity_id": "no-such-entity"}, db_path=graph_db)
        assert "error" in missing

    def test_get_entity_brief_uses_the_entitys_own_kind(self, graph_db):
        """A person brief is only reachable if the lookup keys on the entity's
        kind -- guessing case/project would miss it."""
        found = tools.dispatch("get_entity_brief", {"entity_id": "ent-priya"}, db_path=graph_db)
        assert found["node_type"] == "person"
        assert found["headline"] == "Priya is waiting on an ETA"

    def test_list_entities_filters_by_kind(self, graph_db):
        result = tools.dispatch("list_entities", {"kind": "person"}, db_path=graph_db)
        assert result["entities"]
        assert all(e["kind"] == "person" for e in result["entities"])

    def test_list_entities_matches_name_and_alias(self, graph_db):
        by_name = tools.dispatch("list_entities", {"query": "henderson"}, db_path=graph_db)
        assert [e["entity_id"] for e in by_name["entities"]] == ["ent-henderson"]
        by_alias = tools.dispatch("list_entities", {"query": "hend-4471"}, db_path=graph_db)
        assert [e["entity_id"] for e in by_alias["entities"]] == ["ent-henderson"]

    def test_list_entities_reports_the_untruncated_total(self, graph_db):
        result = tools.dispatch("list_entities", {}, db_path=graph_db)
        assert result["total_matching"] == 2
        assert result["truncated"] is False

    def test_find_open_items_filters_by_case(self, graph_db):
        result = tools.dispatch("find_open_items", {"case": "Henderson"}, db_path=graph_db)
        assert result["items"]
        assert all(i["node_id"] == "ent-henderson" for i in result["items"])

    def test_find_open_items_unfiltered_spans_briefs(self, graph_db):
        result = tools.dispatch("find_open_items", {}, db_path=graph_db)
        assert {i["node_id"] for i in result["items"]} == {"ent-henderson", "ent-priya"}

    def test_find_open_items_unknown_target_is_an_error_not_silence(self, graph_db):
        result = tools.dispatch("find_open_items", {"person": "Nobody At All"}, db_path=graph_db)
        assert "error" in result
