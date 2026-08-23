"""/api/agent/* endpoints — a fake Anthropic-shaped client, no network."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent import conversation as agent_conversation
from api import main

import llm.client as llm_client_module


class _Block:
    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class _Response:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


def _fake_client(responses):
    queue = list(responses)

    def create(**kwargs):
        return queue.pop(0)

    client = type("Client", (), {})()
    client.messages = type("Messages", (), {"create": staticmethod(create)})()
    return client


def _text_only_client(text):
    return _fake_client([_Response([_Block("text", text=text)], "end_turn")])


def _parse_sse(body: str):
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(main, "DB_PATH", db_path)
    return TestClient(main.app)


class TestAgentChat:
    def test_creates_a_conversation_and_streams_a_reply(self, client, monkeypatch):
        monkeypatch.setattr(
            llm_client_module, "get_client",
            lambda stage=None, provider=None: _text_only_client("Nothing urgent today."),
        )
        response = client.post("/api/agent/chat", json={"message": "anything urgent?"})
        assert response.status_code == 200

        events = _parse_sse(response.text)
        assert [e["type"] for e in events] == ["text_delta", "done"]
        assert events[0]["text"] == "Nothing urgent today."
        conversation_id = events[-1]["conversationId"]
        assert conversation_id

        history = agent_conversation.history(conversation_id, db_path=main.DB_PATH)
        assert history[0] == {"role": "user", "content": "anything urgent?"}
        assert history[1]["role"] == "assistant"

    def test_continues_an_existing_conversation(self, client, monkeypatch):
        monkeypatch.setattr(
            llm_client_module, "get_client",
            lambda stage=None, provider=None: _text_only_client("first reply"),
        )
        first = client.post("/api/agent/chat", json={"message": "hi"})
        conversation_id = _parse_sse(first.text)[-1]["conversationId"]

        monkeypatch.setattr(
            llm_client_module, "get_client",
            lambda stage=None, provider=None: _text_only_client("second reply"),
        )
        second = client.post(
            "/api/agent/chat", json={"message": "and then?", "conversationId": conversation_id}
        )
        events = _parse_sse(second.text)
        assert events[-1]["conversationId"] == conversation_id
        assert events[0]["text"] == "second reply"

        history = agent_conversation.history(conversation_id, db_path=main.DB_PATH)
        assert [m["content"] for m in history if m["role"] == "user"] == ["hi", "and then?"]

    def test_tool_calls_surface_as_tool_start_and_tool_end_events(self, client, monkeypatch):
        fake = _fake_client([
            _Response(
                [_Block("tool_use", id="tu1", name="list_queue", input={})], "tool_use"
            ),
            _Response([_Block("text", text="You have 2 emails.")], "end_turn"),
        ])
        monkeypatch.setattr(llm_client_module, "get_client", lambda stage=None, provider=None: fake)
        response = client.post("/api/agent/chat", json={"message": "how many emails?"})
        events = _parse_sse(response.text)
        assert [e["type"] for e in events] == ["tool_start", "tool_end", "text_delta", "done"]
        assert events[0]["tool"] == "list_queue"
        assert "total" in events[1]["toolResult"]

    def test_ollama_provider_surfaces_as_an_error_event_not_a_500(self, client, monkeypatch):
        from llm import config as llm_config

        monkeypatch.setattr(llm_config, "provider_for", lambda stage=None: llm_config.OLLAMA)
        response = client.post("/api/agent/chat", json={"message": "hi"})
        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert events[0]["type"] == "error"

    def test_requires_a_token_once_configured(self, client, monkeypatch):
        monkeypatch.setenv("API_TOKEN", "secret")
        response = client.post("/api/agent/chat", json={"message": "hi"})
        assert response.status_code == 401


class TestGetAgentConversation:
    def test_returns_full_history(self, client, monkeypatch):
        monkeypatch.setattr(
            llm_client_module, "get_client",
            lambda stage=None, provider=None: _text_only_client("reply"),
        )
        chat = client.post("/api/agent/chat", json={"message": "hi"})
        conversation_id = _parse_sse(chat.text)[-1]["conversationId"]

        response = client.get("/api/agent/conversations/{0}".format(conversation_id))
        assert response.status_code == 200
        body = response.json()
        assert body["conversationId"] == conversation_id
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]

    def test_unknown_conversation_is_404(self, client):
        response = client.get("/api/agent/conversations/does-not-exist")
        assert response.status_code == 404
