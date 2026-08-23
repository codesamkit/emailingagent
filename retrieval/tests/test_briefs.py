"""PHASES-COMPLEX.md B4. Fake client only — no network, no model, following
drafting/tests/test_outline_gating.py's FakeClient pattern."""

from __future__ import annotations

import json

from retrieval import briefs
from retrieval.tests.fixtures import build_fixture_db

_CANNED = {
    "reason": "evidence covers root cause, credit, and open follow-ups",
    "headline": "Henderson escalation: root cause found, credit issued",
    "body_md": "Root cause was an Apollo cutover failure. Credit issued.",
    "open_items": ["Send client-facing summary"],
}


class _FakeBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class FakeClient:
    def __init__(self, response: dict = _CANNED):
        self.calls = []
        self._response = response
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(json.dumps(self._response))


def test_single_message_threads_never_get_a_brief():
    path = build_fixture_db()
    client = FakeClient()
    briefs.rebuild_dirty(db_path=path, client=client)

    # thread-henderson-ticket and thread-henderson-participants have exactly
    # one message each — the 2-email gate must exclude them.
    assert briefs.get_brief("thread", "thread-henderson-ticket", db_path=path) is None
    assert briefs.get_brief("thread", "thread-henderson-participants", db_path=path) is None


def test_qualifying_nodes_get_a_generated_brief():
    path = build_fixture_db()
    client = FakeClient()
    rebuilt = briefs.rebuild_dirty(db_path=path, client=client)

    assert rebuilt > 0
    brief = briefs.get_brief("case", "ent-case-henderson", db_path=path)
    assert brief is not None
    assert brief.headline == _CANNED["headline"]
    assert brief.open_items == _CANNED["open_items"]
    assert brief.evidence_hash  # a real hash was stored, not the fixture placeholder


def test_unchanged_hash_results_in_zero_model_calls():
    path = build_fixture_db()
    client = FakeClient()

    first_count = briefs.rebuild_dirty(db_path=path, client=client)
    assert first_count > 0
    calls_after_first = len(client.calls)

    second_count = briefs.rebuild_dirty(db_path=path, client=client)
    assert second_count == 0
    assert len(client.calls) == calls_after_first  # no new calls made


def test_reason_field_declared_before_answer_fields():
    keys = list(briefs.RESPONSE_SCHEMA["properties"].keys())
    assert keys[0] == "reason"


def test_prompt_includes_evidence_from_multiple_emails():
    path = build_fixture_db()
    client = FakeClient()
    briefs.rebuild_dirty(db_path=path, client=client)

    prompts = [call["messages"][0]["content"] for call in client.calls]
    # The Henderson case brief's evidence spans multiple senders — Alex and
    # Jordan both appear somewhere in that one prompt, not split across two.
    assert any(
        "alex@acme.example" in prompt and "jordan@acme.example" in prompt
        for prompt in prompts
    )
