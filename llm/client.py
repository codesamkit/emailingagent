"""One place that decides which LLM backend a stage talks to.

Before this, each of the four call sites had its own `_get_default_client()`
that constructed `anthropic.Anthropic()` directly, so switching providers
meant editing four files. They now all route through `get_client(stage)`,
which reads config and hands back an Anthropic-shaped client — real or local.

The per-stage argument is what makes the hybrid split possible: high-volume
grunt work (classify, score, summarize) can run against a local model while
the handful of reply outlines, where judgment is visible, stays on a hosted
one. Both are the same call site; only config differs.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import config

log = logging.getLogger(__name__)


class UnknownProviderError(ValueError):
    pass


def get_client(stage: Optional[str] = None, provider: Optional[str] = None) -> Any:
    """An Anthropic-shaped client for `stage`.

    Both backends expose `.messages.create(model=..., max_tokens=..., system=...,
    messages=[...], output_config=...)` and return an object whose `.content`
    is a list of blocks with `.type` and `.text`.
    """
    provider = (provider or config.provider_for(stage)).lower()

    if provider == config.OLLAMA:
        from .ollama import OllamaClient

        return OllamaClient(model=config.model_for(stage, provider))

    if provider == config.ANTHROPIC:
        import anthropic

        return anthropic.Anthropic()

    raise UnknownProviderError(
        "Unknown LLM provider {0!r}. Set LLM_PROVIDER to one of {1}.".format(
            provider, ", ".join(config.PROVIDERS)
        )
    )


def model_for(stage: Optional[str] = None) -> str:
    """The model id a stage will use — so call sites stop hardcoding one."""
    return config.model_for(stage)


def describe() -> str:
    """Human-readable routing summary, for the CLI and for debugging."""
    lines = ["default provider: {0}".format(config.DEFAULT_PROVIDER)]
    for stage in config.ROUTABLE_STAGES:
        provider = config.provider_for(stage)
        lines.append(
            "  {0:<10} {1:<10} {2}".format(stage, provider, config.model_for(stage, provider))
        )
    lines.append("ollama host: {0}".format(config.OLLAMA_HOST))
    return "\n".join(lines)
