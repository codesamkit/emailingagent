"""agent/conversation.py: SQLite round-trip, no network, no model calls."""

from __future__ import annotations

import pytest

from agent import conversation


@pytest.fixture
def db(tmp_path):
    return tmp_path / "t.db"


class TestCreate:
    def test_returns_a_usable_id(self, db):
        conversation_id = conversation.create(db_path=db)
        assert conversation_id
        [row] = conversation.recent(db_path=db)
        assert row.conversation_id == conversation_id

    def test_title_is_optional(self, db):
        conversation_id = conversation.create(title="Henderson follow-up", db_path=db)
        [row] = conversation.recent(db_path=db)
        assert row.title == "Henderson follow-up"

    def test_untitled_conversation_has_none_title(self, db):
        conversation.create(db_path=db)
        [row] = conversation.recent(db_path=db)
        assert row.title is None


class TestGet:
    def test_returns_known_conversation(self, db):
        conversation_id = conversation.create(title="x", db_path=db)
        row = conversation.get(conversation_id, db_path=db)
        assert row is not None
        assert row.conversation_id == conversation_id

    def test_unknown_conversation_returns_none(self, db):
        assert conversation.get("nope", db_path=db) is None


class TestAppendAndHistory:
    def test_round_trips_string_content(self, db):
        conversation_id = conversation.create(db_path=db)
        conversation.append(conversation_id, "user", "what's urgent today?", db_path=db)
        history = conversation.history(conversation_id, db_path=db)
        assert history == [{"role": "user", "content": "what's urgent today?"}]

    def test_round_trips_content_block_list(self, db):
        conversation_id = conversation.create(db_path=db)
        blocks = [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "list_queue", "input": {}}]
        conversation.append(conversation_id, "assistant", blocks, db_path=db)
        [message] = conversation.history(conversation_id, db_path=db)
        assert message["content"] == blocks

    def test_history_is_oldest_first(self, db):
        conversation_id = conversation.create(db_path=db)
        conversation.append(conversation_id, "user", "first", db_path=db)
        conversation.append(conversation_id, "assistant", "second", db_path=db)
        conversation.append(conversation_id, "user", "third", db_path=db)
        history = conversation.history(conversation_id, db_path=db)
        assert [m["content"] for m in history] == ["first", "second", "third"]

    def test_limit_keeps_the_most_recent_still_oldest_first(self, db):
        conversation_id = conversation.create(db_path=db)
        for text in ["a", "b", "c", "d"]:
            conversation.append(conversation_id, "user", text, db_path=db)
        history = conversation.history(conversation_id, limit=2, db_path=db)
        assert [m["content"] for m in history] == ["c", "d"]

    def test_invalid_role_raises(self, db):
        conversation_id = conversation.create(db_path=db)
        with pytest.raises(ValueError):
            conversation.append(conversation_id, "system", "nope", db_path=db)

    def test_unknown_conversation_raises(self, db):
        with pytest.raises(ValueError):
            conversation.append("does-not-exist", "user", "hi", db_path=db)

    def test_append_bumps_updated_at(self, db):
        conversation_id = conversation.create(db_path=db)
        [before] = conversation.recent(db_path=db)
        conversation.append(conversation_id, "user", "hi", db_path=db)
        [after] = conversation.recent(db_path=db)
        assert after.updated_at >= before.updated_at


class TestRecent:
    def test_most_recently_updated_first(self, db):
        first = conversation.create(title="first", db_path=db)
        second = conversation.create(title="second", db_path=db)
        conversation.append(first, "user", "bump this one", db_path=db)
        rows = conversation.recent(db_path=db)
        assert [r.conversation_id for r in rows] == [first, second]

    def test_limit_is_respected(self, db):
        for i in range(5):
            conversation.create(title=str(i), db_path=db)
        rows = conversation.recent(limit=2, db_path=db)
        assert len(rows) == 2

    def test_empty_db_returns_empty_list(self, db):
        assert conversation.recent(db_path=db) == []
