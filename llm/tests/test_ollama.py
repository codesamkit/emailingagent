"""The Ollama adapter, against a fake HTTP layer — no server needed."""

from __future__ import annotations

import json

import pytest

from llm import config
from llm.client import UnknownProviderError, get_client
from llm.ollama import OllamaClient, OllamaError, _extract_schema

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
OUTPUT_CONFIG = {"format": {"type": "json_schema", "schema": SCHEMA}}


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records requests and returns scripted responses."""

    def __init__(self, response=None, raise_on_post=None):
        self.response = response or FakeResponse(
            {"message": {"role": "assistant", "content": '{"x": "hi"}'},
             "prompt_eval_count": 12, "eval_count": 5}
        )
        self.raise_on_post = raise_on_post
        self.posts = []
        self.gets = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if self.raise_on_post:
            raise self.raise_on_post
        return self.response

    def get(self, url, timeout=None):
        self.gets.append(url)
        return FakeResponse({"models": [{"name": "gemma2:2b"}, {"name": "llama3.1:8b"}]})


def client(session=None, **kwargs):
    return OllamaClient(session=session or FakeSession(), **kwargs)


class TestAnthropicShapeCompatibility:
    def test_response_matches_the_shape_call_sites_consume(self):
        """Every call site does: next(b.text for b in r.content if b.type=='text')."""
        response = client().messages.create(messages=[{"role": "user", "content": "hi"}])
        text = next(b.text for b in response.content if b.type == "text")
        assert text == '{"x": "hi"}'

    def test_usage_is_reported(self):
        response = client().messages.create(messages=[{"role": "user", "content": "hi"}])
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 5

    def test_unknown_anthropic_only_kwargs_are_ignored_not_fatal(self):
        """thinking/betas/effort have no Ollama equivalent — degrade, don't crash."""
        response = client().messages.create(
            messages=[{"role": "user", "content": "hi"}],
            thinking={"type": "adaptive"},
            betas=["something"],
            effort="high",
        )
        assert response.content[0].text


class TestRequestTranslation:
    def test_system_becomes_a_leading_message(self):
        """Anthropic keeps system separate; Ollama wants it in messages."""
        session = FakeSession()
        client(session).messages.create(
            system="You are terse.", messages=[{"role": "user", "content": "hi"}]
        )
        sent = session.posts[0]["json"]["messages"]
        assert sent[0] == {"role": "system", "content": "You are terse."}
        assert sent[1] == {"role": "user", "content": "hi"}

    def test_no_system_message_when_none_given(self):
        session = FakeSession()
        client(session).messages.create(messages=[{"role": "user", "content": "hi"}])
        assert session.posts[0]["json"]["messages"][0]["role"] == "user"

    def test_json_schema_is_passed_as_ollama_format(self):
        """This is what makes Track B's structured outputs work locally."""
        session = FakeSession()
        client(session).messages.create(
            messages=[{"role": "user", "content": "hi"}], output_config=OUTPUT_CONFIG
        )
        assert session.posts[0]["json"]["format"] == SCHEMA

    def test_no_format_key_when_no_schema_requested(self):
        session = FakeSession()
        client(session).messages.create(messages=[{"role": "user", "content": "hi"}])
        assert "format" not in session.posts[0]["json"]

    def test_max_tokens_becomes_num_predict(self):
        session = FakeSession()
        client(session).messages.create(
            messages=[{"role": "user", "content": "hi"}], max_tokens=512
        )
        assert session.posts[0]["json"]["options"]["num_predict"] == 512

    def test_streaming_is_off(self):
        session = FakeSession()
        client(session).messages.create(messages=[{"role": "user", "content": "hi"}])
        assert session.posts[0]["json"]["stream"] is False

    def test_content_block_lists_are_flattened(self):
        session = FakeSession()
        client(session).messages.create(
            messages=[{"role": "user", "content": [{"type": "text", "text": "a"},
                                                   {"type": "text", "text": "b"}]}]
        )
        assert session.posts[0]["json"]["messages"][0]["content"] == "ab"

    def test_model_override_wins_over_the_configured_default(self):
        session = FakeSession()
        client(session, model="gemma2:2b").messages.create(
            model="llama3.1:8b", messages=[{"role": "user", "content": "hi"}]
        )
        assert session.posts[0]["json"]["model"] == "llama3.1:8b"

    def test_host_trailing_slash_is_normalized(self):
        session = FakeSession()
        client(session, host="http://pc.local:11434/").messages.create(
            messages=[{"role": "user", "content": "hi"}]
        )
        assert session.posts[0]["url"] == "http://pc.local:11434/api/chat"


class TestErrorHandling:
    def test_unreachable_host_explains_the_loopback_gotcha(self):
        session = FakeSession(raise_on_post=ConnectionError("refused"))
        with pytest.raises(OllamaError) as exc:
            client(session).messages.create(messages=[{"role": "user", "content": "hi"}])
        assert "OLLAMA_HOST=0.0.0.0" in str(exc.value)

    def test_http_error_is_surfaced(self):
        session = FakeSession(response=FakeResponse({}, status_code=500, text="boom"))
        with pytest.raises(OllamaError) as exc:
            client(session).messages.create(messages=[{"role": "user", "content": "hi"}])
        assert "500" in str(exc.value)

    def test_empty_reply_suggests_pulling_the_model(self):
        """The usual cause, and otherwise an opaque failure."""
        session = FakeSession(response=FakeResponse({"message": {"content": "  "}}))
        with pytest.raises(OllamaError) as exc:
            client(session, model="gemma2:2b").messages.create(
                messages=[{"role": "user", "content": "hi"}])
        assert "ollama pull gemma2:2b" in str(exc.value)

    def test_non_json_reply_is_reported(self):
        session = FakeSession(response=FakeResponse(None, text="<html>"))
        with pytest.raises(OllamaError):
            client(session).messages.create(messages=[{"role": "user", "content": "hi"}])


class TestCheck:
    def test_reports_pulled_models(self):
        info = client(model="gemma2:2b").check()
        assert info["model_present"] is True
        assert "llama3.1:8b" in info["available"]

    def test_matches_on_base_name_without_tag(self):
        """Users configure 'gemma2'; ollama reports 'gemma2:2b'."""
        assert client(model="gemma2").check()["model_present"] is True

    def test_missing_model_is_reported(self):
        assert client(model="mistral:7b").check()["model_present"] is False


class TestSchemaExtraction:
    def test_extracts_nested_schema(self):
        assert _extract_schema(OUTPUT_CONFIG) == SCHEMA

    def test_none_for_missing_config(self):
        assert _extract_schema(None) is None
        assert _extract_schema({}) is None

    def test_none_for_non_json_schema_format(self):
        assert _extract_schema({"format": {"type": "text"}}) is None


class TestRouting:
    def test_ollama_provider_returns_the_adapter(self):
        assert isinstance(get_client("summarize", provider="ollama"), OllamaClient)

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(UnknownProviderError):
            get_client("summarize", provider="llamafile")

    def test_per_stage_override(self, monkeypatch):
        """The hybrid split: grunt work local, outlines hosted."""
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setattr(config, "DEFAULT_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_PROVIDER_OUTLINE", "anthropic")
        assert config.provider_for("summarize") == "ollama"
        assert config.provider_for("outline") == "anthropic"

    def test_model_follows_the_provider(self, monkeypatch):
        monkeypatch.setattr(config, "OLLAMA_MODEL", "gemma2:2b")
        assert config.model_for("summarize", "ollama") == "gemma2:2b"
        assert config.model_for("outline", "anthropic") == config.ANTHROPIC_MODEL

    def test_per_stage_model_override(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL_SCORE", "qwen2.5:14b")
        assert config.model_for("score", "ollama") == "qwen2.5:14b"
