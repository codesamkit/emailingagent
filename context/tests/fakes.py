"""Stand-ins for everything the context package talks to — no network, no model.

Same posture as `calendaring/tests/fakes.py`: shaped like the real thing, so
the module under test runs through exactly the call path it uses in production.
One file for the package rather than a copy of the same six-line builder in
every test module.
"""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from models.schema import RawEmail, ReadStatus


def raw(
    email_id: str = "e1",
    *,
    thread_id: str = "t1",
    sender: str = "sasha.petrova@stridecore.com",
    recipients: Optional[Sequence[str]] = None,
    subject: str = "Dock firmware ownership",
    body: str = "Short body.",
    received_at: Optional[datetime] = None,
    read_status: ReadStatus = ReadStatus.READ,
    headers: Optional[Dict[str, str]] = None,
) -> RawEmail:
    return RawEmail(
        email_id=email_id,
        thread_id=thread_id,
        sender=sender,
        recipients=list(recipients if recipients is not None else ["me@example.com"]),
        subject=subject,
        body=body,
        received_at=received_at or datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc),
        read_status=read_status,
        headers=dict(headers or {}),
    )


# --- LLM client -----------------------------------------------------------

class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, payload: Any):
        self.content = [_Block(payload if isinstance(payload, str) else json.dumps(payload))]


class _Messages:
    def __init__(self, payloads: List[Any]):
        self._payloads = payloads
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Last entry is reused once exhausted, so a one-element list means
        # "always return this" — same convention as FakeCalendarService.
        payload = self._payloads[0] if len(self._payloads) == 1 else self._payloads.pop(0)
        return _Response(payload)


class FakeClient:
    """A scripted Anthropic-shaped client. `messages.calls` records every call."""

    def __init__(self, *payloads: Any):
        self.messages = _Messages(list(payloads) or [{}])

    @property
    def call_count(self) -> int:
        return len(self.messages.calls)

    @property
    def last_user_message(self) -> str:
        content = self.messages.calls[-1]["messages"][-1]["content"]
        if isinstance(content, list):
            return "".join(block.get("text", "") for block in content)
        return content


class ExplodingClient:
    """Any use of this fails the test — proves no model call was made."""

    def __getattr__(self, name):
        raise AssertionError(
            "An LLM call was made (.{0}) when it should not have been".format(name)
        )


# --- vectors --------------------------------------------------------------

def vec(*values: float) -> bytes:
    """A normalized float32 little-endian blob, the on-disk vector format.

    Hand-built low-dimensional vectors are the point: the resolution threshold
    is a guess that has to be tunable against cases you can reason about by
    eye, which is impossible with 768 real dimensions.
    """
    norm = sum(v * v for v in values) ** 0.5 or 1.0
    return struct.pack("<{0}f".format(len(values)), *[v / norm for v in values])
