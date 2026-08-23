"""agent/loop.py: a scripted fake Anthropic-shaped client, no network."""

from __future__ import annotations

import pytest

import agent.tools as tools_module
from agent import loop


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


def _scripted_client(responses):
    """Returns one canned Response per call, in order."""
    queue = list(responses)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return queue.pop(0)

    client = type("Client", (), {})()
    client.messages = type("Messages", (), {"create": staticmethod(create)})()
    client.calls = calls
    return client


def _text_response(text, stop_reason="end_turn"):
    return _Response([_Block("text", text=text)], stop_reason)


def _tool_use_response(name, tool_input, tool_use_id="tu1"):
    return _Response(
        [_Block("tool_use", id=tool_use_id, name=name, input=tool_input)],
        "tool_use",
    )


class TestZeroToolCalls:
    def test_single_text_turn(self):
        client = _scripted_client([_text_response("Hi there.")])
        events = list(loop.run([{"role": "user", "content": "hi"}], client=client))

        assert [e.type for e in events] == ["text_delta", "done"]
        assert events[0].text == "Hi there."
        assert len(client.calls) == 1
        assert client.calls[0]["tools"] == tools_module.TOOL_SPECS
        done = events[-1]
        assert done.new_messages == [{"role": "assistant", "content": [{"type": "text", "text": "Hi there."}]}]


class TestOneToolCall:
    def test_dispatches_and_continues(self, monkeypatch):
        seen = {}

        def fake_dispatch(name, args, *, db_path=None):
            seen["name"], seen["args"] = name, args
            return {"total": 0, "emails": []}

        monkeypatch.setattr(tools_module, "dispatch", fake_dispatch)

        client = _scripted_client([
            _tool_use_response("list_queue", {"importance": "urgent"}),
            _text_response("Nothing urgent right now."),
        ])
        events = list(loop.run([{"role": "user", "content": "anything urgent?"}], client=client))

        assert [e.type for e in events] == ["tool_start", "tool_end", "text_delta", "done"]
        assert seen == {"name": "list_queue", "args": {"importance": "urgent"}}
        assert events[0].tool == "list_queue"
        assert events[1].tool_result == {"total": 0, "emails": []}
        assert len(client.calls) == 2

    def test_new_messages_include_tool_result_turn(self, monkeypatch):
        monkeypatch.setattr(tools_module, "dispatch", lambda name, args, **kw: {"ok": True})
        client = _scripted_client([
            _tool_use_response("get_email", {"email_id": "e1"}),
            _text_response("Here it is."),
        ])
        events = list(loop.run([{"role": "user", "content": "show e1"}], client=client))
        done = events[-1]
        assert len(done.new_messages) == 3  # assistant(tool_use), user(tool_result), assistant(text)
        assert done.new_messages[1]["role"] == "user"
        assert done.new_messages[1]["content"][0]["type"] == "tool_result"


class TestChainOfThree:
    def test_three_tool_calls_then_a_final_answer(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            tools_module, "dispatch",
            lambda name, args, **kw: calls.append(name) or {"n": len(calls)},
        )
        client = _scripted_client([
            _tool_use_response("list_queue", {}, tool_use_id="tu1"),
            _tool_use_response("get_email", {"email_id": "e1"}, tool_use_id="tu2"),
            _tool_use_response("search_context", {"query": "x"}, tool_use_id="tu3"),
            _text_response("Done investigating."),
        ])
        events = list(loop.run([{"role": "user", "content": "investigate"}], client=client))

        tool_starts = [e for e in events if e.type == "tool_start"]
        assert [e.tool for e in tool_starts] == ["list_queue", "get_email", "search_context"]
        assert calls == ["list_queue", "get_email", "search_context"]
        assert events[-1].type == "done"
        assert len(client.calls) == 4


class TestMaxTurns:
    def test_stops_at_max_turns_even_if_still_asking_for_tools(self, monkeypatch):
        monkeypatch.setattr(tools_module, "dispatch", lambda name, args, **kw: {})
        # Every response asks for another tool call -- an infinite loop
        # without the max_turns cap. The last entry answers the forced
        # final call the loop makes once the budget is spent.
        client = _scripted_client(
            [
                _tool_use_response("list_queue", {}, tool_use_id="tu{0}".format(i))
                for i in range(10)
            ]
            + [_text_response("Here is what I found.")]
        )
        events = list(
            loop.run([{"role": "user", "content": "loop forever"}], client=client, max_turns=2)
        )
        # 2 tool-use turns + 1 forced synthesis turn.
        assert len(client.calls) == 3
        assert events[-1].type == "done"

    def test_exhausting_the_budget_still_produces_an_answer(self, monkeypatch):
        """Running out of turns mid-tool-use used to yield no assistant text at
        all -- the caller saw a silent `done` after N tool calls."""
        monkeypatch.setattr(tools_module, "dispatch", lambda name, args, **kw: {})
        client = _scripted_client(
            [_tool_use_response("list_queue", {}), _text_response("Partial answer.")]
        )
        events = list(
            loop.run([{"role": "user", "content": "go"}], client=client, max_turns=1)
        )

        assert [e.text for e in events if e.type == "text_delta"] == ["Partial answer."]
        # The forced call must leave the model no way to ask for more tools.
        assert client.calls[-1]["tool_choice"] == {"type": "none"}
        # ...and it is persisted like any other turn, so the next message in
        # the conversation continues from it.
        assert events[-1].new_messages[-1]["role"] == "assistant"

    def test_default_max_turns_is_twelve(self):
        import inspect

        assert inspect.signature(loop.run).parameters["max_turns"].default == 12


class TestOllamaGuard:
    def test_raises_before_any_api_call_when_agent_routes_to_ollama(self, monkeypatch):
        from llm import config as llm_config

        monkeypatch.setattr(llm_config, "provider_for", lambda stage=None: llm_config.OLLAMA)

        with pytest.raises(loop.OllamaToolsUnsupportedError):
            list(loop.run([{"role": "user", "content": "hi"}]))

    def test_injected_client_bypasses_the_provider_check(self, monkeypatch):
        """An explicitly-injected client (tests, or a future non-default
        caller) is an intentional override -- the guard only applies to the
        auto-resolved path."""
        from llm import config as llm_config

        monkeypatch.setattr(llm_config, "provider_for", lambda stage=None: llm_config.OLLAMA)
        client = _scripted_client([_text_response("fine")])
        events = list(loop.run([{"role": "user", "content": "hi"}], client=client))
        assert events[-1].type == "done"
