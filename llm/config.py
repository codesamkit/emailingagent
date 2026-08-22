"""Which LLM backend each stage talks to.

Every value is environment-overridable so switching providers is a config
change, not a code change — and so a stage can be pointed somewhere different
from the rest (local model for the high-volume grunt work, hosted model for
the handful of calls where judgment shows).
"""

from __future__ import annotations

import os
from typing import Optional

ANTHROPIC = "anthropic"
OLLAMA = "ollama"
PROVIDERS = (ANTHROPIC, OLLAMA)

# The stages that can be routed independently. Names match pipeline STAGES.
ROUTABLE_STAGES = ("classify", "score", "summarize", "outline")


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


# Default backend for every stage.
DEFAULT_PROVIDER = (_clean(os.environ.get("LLM_PROVIDER")) or ANTHROPIC).lower()

# --- Ollama ---------------------------------------------------------------
# A plain URL, so localhost, a LAN address, and a Tailscale address are all
# the same thing to this code.
OLLAMA_HOST = _clean(os.environ.get("OLLAMA_HOST")) or "http://localhost:11434"
OLLAMA_MODEL = _clean(os.environ.get("OLLAMA_MODEL")) or "llama3.1:8b"
# Local inference on a big prompt is slow compared to a hosted API; the
# default here is generous on purpose so a cold model load isn't read as a
# failure.
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT") or 300)

# --- Anthropic -------------------------------------------------------------
ANTHROPIC_MODEL = _clean(os.environ.get("ANTHROPIC_MODEL")) or "claude-opus-5"


def provider_for(stage: Optional[str] = None) -> str:
    """The backend for one stage.

    `LLM_PROVIDER_OUTLINE=anthropic` overrides `LLM_PROVIDER=ollama` for the
    outline stage alone — the hybrid split, expressed as two env vars rather
    than a code branch.
    """
    if stage:
        override = _clean(os.environ.get("LLM_PROVIDER_{0}".format(stage.upper())))
        if override:
            return override.lower()
    return DEFAULT_PROVIDER


def model_for(stage: Optional[str] = None, provider: Optional[str] = None) -> str:
    """The model id for one stage, honoring a per-stage override."""
    provider = (provider or provider_for(stage)).lower()
    if stage:
        override = _clean(os.environ.get("LLM_MODEL_{0}".format(stage.upper())))
        if override:
            return override
    return OLLAMA_MODEL if provider == OLLAMA else ANTHROPIC_MODEL
