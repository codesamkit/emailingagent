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


class TestStubbedRetrievalTools:
    """search_context / get_thread_brief / get_entity_brief / list_entities /
    find_open_items all run against agent/fixtures.py's hand-written data
    until Track A/B exist. These just prove the seam works end-to-end."""

    def test_search_context_returns_bounded_sections(self, db):
        result = tools.dispatch("search_context", {"query": "henderson"}, db_path=db)
        assert "sections" in result
        assert len(result["sections"]) <= tools.MAX_LIST_ITEMS

    def test_get_thread_brief_known_and_unknown(self, db):
        found = tools.dispatch("get_thread_brief", {"thread_id": "thread-scheduling"}, db_path=db)
        assert "error" not in found
        missing = tools.dispatch("get_thread_brief", {"thread_id": "no-such-thread"}, db_path=db)
        assert "error" in missing

    def test_get_entity_brief_known_and_unknown(self, db):
        found = tools.dispatch("get_entity_brief", {"entity_id": "ent-henderson"}, db_path=db)
        assert "error" not in found
        missing = tools.dispatch("get_entity_brief", {"entity_id": "no-such-entity"}, db_path=db)
        assert "error" in missing

    def test_list_entities_filters_by_kind(self, db):
        result = tools.dispatch("list_entities", {"kind": "person"}, db_path=db)
        assert all(e["kind"] == "person" for e in result["entities"])

    def test_find_open_items_filters_by_case(self, db):
        result = tools.dispatch("find_open_items", {"case": "ent-henderson"}, db_path=db)
        assert all(i["nodeId"] == "ent-henderson" for i in result["items"])
