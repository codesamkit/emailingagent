"""The agent's tool-use loop.

Standard Anthropic tool-use loop: call messages.create with tools=TOOL_SPECS,
and while stop_reason == "tool_use", dispatch each tool_use block through
agent.tools.dispatch and append a tool_result block, repeating until the
model stops asking for tools or max_turns is hit. Yields events so a
transport layer (api/main.py's chat endpoint) can stream progress out.

Uses client.messages.create() once per turn — the same non-streaming call
shape every other LLM call site in this repo already uses (categorize.py,
expand.py, ...) — rather than a token-level streaming API nothing else here
uses. "Streaming" to the caller happens at turn granularity: one text_delta
per assistant turn, plus tool_start/tool_end around each tool call, which is
still real incremental progress across up to max_turns turns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Valence, an assistant over this user's mailbox. Always ground "
    "your answers in tool results and cite email_ids for any claim about a "
    "specific email. Never claim to have sent anything — you cannot send "
    "email. When asked to draft a reply, call draft_reply and return the "
    "draft for the user to review, not as something already sent."
)


class OllamaToolsUnsupportedError(RuntimeError):
    """Raised before any API call if the resolved 'agent' provider is
    ollama. llm/ollama.py's create() has no tools= support and silently
    ignores unknown keyword arguments (see its **_ignored) — without this
    guard, an ollama-routed agent would confidently answer having called no
    tools at all, which is worse than an explicit failure."""


@dataclass
class Event:
    type: str  # "text_delta" | "tool_start" | "tool_end" | "done"
    text: Optional[str] = None
    tool: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_result: Optional[Dict[str, Any]] = None
    # "done" only: every message appended during this run (assistant turns
    # and the interleaved tool-result "user" turns) — NOT including the
    # messages the caller passed in. The caller (api/main.py) persists each
    # of these via agent.conversation.append so the next turn has them.
    new_messages: List[Dict[str, Any]] = field(default_factory=list)


def _block_to_dict(block: Any) -> Dict[str, Any]:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    # Forward-compatible with a block type this loop doesn't specifically
    # handle (e.g. a future "thinking" block) rather than crashing on it.
    return {"type": block.type}


def run(
    messages: List[Dict[str, Any]],
    *,
    max_turns: int = 8,
    db_path: Optional[Any] = None,
    client: Optional[Any] = None,
) -> Iterator[Event]:
    """Run the tool-use loop starting from `messages` (which must already
    end with the latest user turn). Yields Events; the last one is always
    type "done"."""
    if client is None:
        from llm import config as llm_config

        provider = llm_config.provider_for("agent")
        if provider == llm_config.OLLAMA:
            raise OllamaToolsUnsupportedError(
                "The 'agent' stage is routed to ollama ({0}), which has no "
                "tool-calling support. Set LLM_PROVIDER_AGENT=anthropic (or "
                "LLM_PROVIDER=anthropic) before using the agent.".format(
                    llm_config.OLLAMA
                )
            )
        from llm.client import get_client, model_for

        client = get_client("agent")
        model = model_for("agent")
    else:
        from llm.client import model_for

        model = model_for("agent")

    from agent.tools import TOOL_SPECS, dispatch

    conversation = list(messages)
    new_messages: List[Dict[str, Any]] = []

    for _turn in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=1536,
            system=SYSTEM_PROMPT,
            messages=conversation,
            tools=TOOL_SPECS,
        )

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        assistant_message = {"role": "assistant", "content": assistant_blocks}
        conversation.append(assistant_message)
        new_messages.append(assistant_message)

        for block in assistant_blocks:
            if block["type"] == "text" and block["text"]:
                yield Event(type="text_delta", text=block["text"])

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason != "tool_use":
            break

        tool_use_blocks = [b for b in assistant_blocks if b["type"] == "tool_use"]
        if not tool_use_blocks:
            # Model claimed tool_use but produced no tool_use block -- treat
            # as done rather than looping forever on nothing to dispatch.
            break

        tool_result_content = []
        for block in tool_use_blocks:
            yield Event(type="tool_start", tool=block["name"], tool_input=block["input"])
            result = dispatch(block["name"], block["input"] or {}, db_path=db_path)
            yield Event(type="tool_end", tool=block["name"], tool_result=result)
            tool_result_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(result),
                }
            )

        tool_result_message = {"role": "user", "content": tool_result_content}
        conversation.append(tool_result_message)
        new_messages.append(tool_result_message)
    else:
        log.info("agent.loop.run hit max_turns=%s without a final answer", max_turns)

    yield Event(type="done", new_messages=new_messages)
