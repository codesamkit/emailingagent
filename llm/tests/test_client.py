"""get_client fails fast and clearly when a provider has no way to
authenticate, instead of the caller finding out deep inside a .create() call."""

from __future__ import annotations

import pytest

from llm import client, config


def test_anthropic_without_a_key_raises_a_clear_runtime_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        client.get_client(provider=config.ANTHROPIC)


def test_anthropic_with_an_api_key_constructs_the_real_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    result = client.get_client(provider=config.ANTHROPIC)

    import anthropic

    assert isinstance(result, anthropic.Anthropic)


def test_anthropic_with_only_an_auth_token_is_also_accepted(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token-not-real")

    result = client.get_client(provider=config.ANTHROPIC)

    import anthropic

    assert isinstance(result, anthropic.Anthropic)
