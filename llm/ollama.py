"""An Anthropic-shaped client backed by a local (or remote) Ollama server.

Deliberately mimics the small slice of the Anthropic SDK this repo actually
uses — `client.messages.create(...)` returning an object whose `.content` is
a list of blocks with `.type` and `.text`. That shape is what every call site
already consumes, so switching providers needs no change to the prompt code:

    response = client.messages.create(...)
    text = next(b.text for b in response.content if b.type == "text")

Ollama's `format` parameter takes a JSON Schema and constrains decoding to
match it, which is what makes the structured-output contract in Track B work
unchanged against a local model.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from . import config

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Ollama was unreachable or returned something unusable."""


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Response:
    """Structurally compatible with an Anthropic Message for our purposes."""

    content: List[TextBlock]
    model: str = ""
    stop_reason: Optional[str] = "end_turn"
    usage: Usage = field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if b.type == "text")


def _extract_schema(output_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pull the JSON Schema out of Anthropic's output_config shape.

    Anthropic nests it as {"format": {"type": "json_schema", "schema": {...}}};
    Ollama wants the bare schema under `format`.
    """
    if not output_config:
        return None
    fmt = output_config.get("format") or {}
    if fmt.get("type") == "json_schema":
        return fmt.get("schema")
    return None


class _Messages:
    def __init__(self, client: "OllamaClient"):
        self._client = client

    def create(
        self,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        output_config: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        **_ignored: Any,
    ) -> Response:
        """Mirror of `anthropic.Anthropic().messages.create` for our usage.

        Unknown keyword arguments are ignored rather than raising: Anthropic-
        only parameters (thinking, betas, effort) have no Ollama equivalent,
        and a call site passing one should degrade, not crash.
        """
        payload: Dict[str, Any] = {
            "model": model or self._client.model,
            "messages": self._client._to_messages(system, messages or []),
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        schema = _extract_schema(output_config)
        if schema is not None:
            # Constrained decoding: the model can only emit tokens that keep
            # the output valid against this schema.
            payload["format"] = schema

        data = self._client._post("/api/chat", payload)
        content = ((data.get("message") or {}).get("content") or "").strip()
        if not content:
            raise OllamaError(
                "Ollama returned an empty response (model={0!r}). The model may "
                "not be pulled — try: ollama pull {0}".format(payload["model"])
            )

        return Response(
            content=[TextBlock(text=content)],
            model=payload["model"],
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
            ),
        )


class OllamaClient:
    """Minimal Ollama client with an Anthropic-compatible surface."""

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[Any] = None,
    ):
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout if timeout is not None else config.OLLAMA_TIMEOUT
        self._session = session or requests

    # --- transport ---------------------------------------------------------

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.host + path
        try:
            response = self._session.post(url, json=payload, timeout=self.timeout)
        except Exception as exc:
            raise OllamaError(
                "Could not reach Ollama at {0} ({1}). Is it running? For a "
                "remote host, the server needs OLLAMA_HOST=0.0.0.0 so it is "
                "not bound to loopback.".format(self.host, exc)
            )
        if response.status_code >= 400:
            raise OllamaError(
                "Ollama returned HTTP {0}: {1}".format(
                    response.status_code, (response.text or "")[:300]
                )
            )
        try:
            return response.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned non-JSON: {0}".format(exc))

    @staticmethod
    def _to_messages(
        system: Optional[str], messages: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """Anthropic keeps `system` separate; Ollama wants it as a message."""
        out: List[Dict[str, str]] = []
        if system:
            out.append({"role": "system", "content": system})
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                # Anthropic allows a content-block list; flatten to text.
                content = "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict)
                )
            out.append({"role": message.get("role", "user"), "content": content or ""})
        return out

    # --- diagnostics -------------------------------------------------------

    @property
    def messages(self) -> _Messages:
        return _Messages(self)

    def available_models(self) -> List[str]:
        try:
            response = self._session.get(self.host + "/api/tags", timeout=10)
            return [m.get("name", "") for m in (response.json().get("models") or [])]
        except Exception as exc:
            raise OllamaError("Could not list models at {0}: {1}".format(self.host, exc))

    def check(self) -> Dict[str, Any]:
        """Is the server up, and is the configured model actually pulled?

        Worth its own method: "model not pulled" and "server down" produce very
        different fixes, and both otherwise surface as an opaque empty reply.
        """
        models = self.available_models()
        # Ollama reports "llama3.1:8b"; users often configure "llama3.1".
        present = any(m == self.model or m.split(":")[0] == self.model.split(":")[0]
                      for m in models)
        return {"host": self.host, "model": self.model, "available": models, "model_present": present}
