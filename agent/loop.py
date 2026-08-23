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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger(__name__)

# Ceiling on tools run at once within a single turn.
MAX_PARALLEL_TOOLS = 8

SYSTEM_PROMPT = (
    "You are Valence, an assistant over this user's mailbox. Always ground "
    "your answers in tool results and cite email_ids for any claim about a "
    "specific email. Never claim to have sent anything — you cannot send "
    "email. When asked to draft a reply, call draft_reply and return the "
    "draft for the user to review, not as something already sent."
    "\n\n"
    "Answer briefly by default. Lead with the answer itself, and prefer a "
    "few tight bullets over prose. Cover only what was asked — do not add "
    "background, caveats, or adjacent findings the user did not ask for. "
    "When a question spans many items, give the headline for each rather "
    "than a full write-up, and offer to go deeper on any one of them. "
    "Expand only when the user asks for detail."
    "\n\n"
    "Prefer get_entity_brief and get_thread_brief over re-reading the "
    "underlying emails: a brief is a standing rollup and is both cheaper "
    "and more current than reconstructing the same picture from scratch. "
    "Reach for search_context or get_email when no brief exists, when the "
    "brief is missing something specific, or when you need to quote."
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
    if block.type == "thinking":
        # Thinking blocks have to go back to the API verbatim, signature
        # included, to continue a tool-use loop on the same model. Echoing a
        # partial one is rejected with
        # "messages.N.content.0.thinking.thinking: Field required" — which is
        # why every field is copied rather than just the type.
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if block.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    # Forward-compatible with a block type this loop doesn't specifically
    # handle rather than crashing on it. Keep every field the SDK gave us:
    # dropping fields is exactly what makes a block unreplayable.
    dump = getattr(block, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else {"type": block.type}


def _separated(event: Event, already_emitted: bool) -> Event:
    """Open a turn's first text with a blank line once anything has been said.

    `already_emitted` means "text has been sent AND this is the first delta of
    a new turn" -- deltas mid-turn must pass through untouched or the answer
    gets a blank line between every token.

    The caller concatenates deltas into one document, so a turn that says
    "I'll look that up." followed by a turn opening "## Projects" ran together
    as "...look that up.## Projects" -- the heading no longer starts a line
    and renders as literal text. Only the first delta of a later turn needs
    it; deltas within a turn already carry their own whitespace.
    """
    if not already_emitted or not event.text or event.text.startswith("\n"):
        return event
    return Event(type=event.type, text="\n\n" + event.text)


def _stream_turn(client: Any, kwargs: Dict[str, Any]) -> Iterator[Any]:
    """One model turn, yielding Event text deltas as they generate and then
    the finished message object last.

    Streaming is what makes the agent feel responsive: a turn that writes a
    long answer took its full generation time before a single character
    reached the caller. The token deltas are the same text either way.

    Falls back to a plain create() when the client has no .stream — the
    Ollama client and the fakes in agent/tests both take that path.
    """
    stream = getattr(client.messages, "stream", None)
    if stream is None:
        yield client.messages.create(**kwargs)
        return

    with stream(**kwargs) as active:
        for event in active:
            # The SDK synthesizes a "text" event per delta; every other event
            # (thinking, tool-input json, block start/stop) is reconstructed
            # from the final message, so only text needs forwarding.
            if getattr(event, "type", None) == "text":
                text = getattr(event, "text", "")
                if text:
                    yield Event(type="text_delta", text=text)
        yield active.get_final_message()


def _dispatch_all(
    blocks: List[Dict[str, Any]], dispatch: Any, db_path: Optional[Any]
) -> List[Dict[str, Any]]:
    """Run one turn's tool calls concurrently, returning results in `blocks`
    order. A single call skips the pool entirely -- the common case, and not
    worth a thread for."""
    if len(blocks) == 1:
        return [dispatch(blocks[0]["name"], blocks[0]["input"] or {}, db_path=db_path)]

    # Bounded so a wide fan-out of model-backed tools doesn't open an
    # unbounded number of concurrent upstream requests.
    workers = min(len(blocks), MAX_PARALLEL_TOOLS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(dispatch, block["name"], block["input"] or {}, db_path=db_path)
            for block in blocks
        ]
        # dispatch() already converts a tool failure into {"error": ...}, so a
        # raise here would be a bug in dispatch itself rather than a tool --
        # still caught, because one of them must not lose the whole turn.
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                results.append({"error": "{0}: {1}".format(type(exc).__name__, exc)})
    return results


def run(
    messages: List[Dict[str, Any]],
    *,
    max_turns: int = 12,
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
    # Whether any assistant text has reached the caller yet -- see _separated.
    emitted_text = False

    for _turn in range(max_turns):
        turn_kwargs = dict(
            model=model,
            # Thinking is on by default on the current models and its
            # tokens count against this budget, so a multi-tool turn needs
            # real headroom — at 1536 a turn can spend the whole budget
            # reasoning and come back truncated.
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=conversation,
            tools=TOOL_SPECS,
            # Every turn re-sends the whole conversation, so by the last turn
            # the tool specs, system prompt, and every prior tool result are
            # being re-read from scratch. Caching the prefix means each turn
            # only pays full price for what that turn added.
            cache_control={"type": "ephemeral"},
        )

        streamed = False
        first_of_turn = True
        for item in _stream_turn(client, turn_kwargs):
            if isinstance(item, Event):
                streamed = True
                yield _separated(item, emitted_text and first_of_turn)
                first_of_turn = False
                emitted_text = True
            else:
                response = item

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        assistant_message = {"role": "assistant", "content": assistant_blocks}
        conversation.append(assistant_message)
        new_messages.append(assistant_message)

        # Already emitted token-by-token while the turn was generating; re-
        # emitting the finished blocks here would duplicate the whole answer.
        if not streamed:
            for block in assistant_blocks:
                if block["type"] == "text" and block["text"]:
                    yield _separated(
                        Event(type="text_delta", text=block["text"]), emitted_text
                    )
                    emitted_text = True

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason != "tool_use":
            break

        tool_use_blocks = [b for b in assistant_blocks if b["type"] == "tool_use"]
        if not tool_use_blocks:
            # Model claimed tool_use but produced no tool_use block -- treat
            # as done rather than looping forever on nothing to dispatch.
            break

        # The model asks for several tools in one turn on purpose; running
        # them one after another made the turn cost the SUM of their
        # latencies. That is the dominant cost whenever a batch lands on a
        # tool that itself calls a model (summarize_selection), where each
        # call is seconds rather than milliseconds. Every tool is a
        # read-only query and models.db.connect hands out a fresh
        # connection per call, so they are safe to run at once.
        for block in tool_use_blocks:
            yield Event(type="tool_start", tool=block["name"], tool_input=block["input"])

        results = _dispatch_all(tool_use_blocks, dispatch, db_path)

        tool_result_content = []
        # Reported and appended in the model's original block order, so a
        # turn's transcript does not depend on which tool happened to
        # finish first.
        for block, result in zip(tool_use_blocks, results):
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
        # The turn budget ran out mid-tool-use, so the model never got a turn
        # to write its answer and the caller would otherwise get silence after
        # a dozen tool calls. One more call with tool_choice "none" forces it
        # to answer from what it already gathered instead of asking for more.
        log.info("agent.loop.run hit max_turns=%s; forcing a final answer", max_turns)
        final_kwargs = dict(
            model=model,
            max_tokens=1536,
            system=SYSTEM_PROMPT,
            messages=conversation,
            tools=TOOL_SPECS,
            tool_choice={"type": "none"},
            cache_control={"type": "ephemeral"},
        )
        streamed = False
        first_of_turn = True
        for item in _stream_turn(client, final_kwargs):
            if isinstance(item, Event):
                streamed = True
                yield _separated(item, emitted_text and first_of_turn)
                first_of_turn = False
                emitted_text = True
            else:
                response = item

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        assistant_message = {"role": "assistant", "content": assistant_blocks}
        new_messages.append(assistant_message)
        if not streamed:
            for block in assistant_blocks:
                if block["type"] == "text" and block["text"]:
                    yield _separated(
                        Event(type="text_delta", text=block["text"]), emitted_text
                    )
                    emitted_text = True

    yield Event(type="done", new_messages=new_messages)
