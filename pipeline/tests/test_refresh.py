"""refresh_one: the live single-email path the extension calls the moment a
previously-unread email is opened. Must stay fast — no expand stage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.schema import ProcessedEmail, ProposedEventStatus, ReadStatus, ReplyOutlineStatus
from pipeline import persist, refresh
from pipeline.orchestrate import Pipeline

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _raw_email(email_id="e1"):
    from ingestion.models import RawEmail  # adds snippet/label_ids/has_attachments store.py needs

    return RawEmail(
        email_id=email_id, thread_id="t1", sender="Dana <dana@example.com>",
        recipients=["me@example.com"], subject="Hi", body="Body text",
        received_at=NOW, read_status=ReadStatus.READ,
    )


@pytest.fixture(autouse=True)
def _stub_gmail(monkeypatch):
    """refresh_one talks to the real Gmail API by design — stub the three
    calls it makes so tests never touch the network."""
    import ingestion.fetch as fetch_module
    import ingestion.gmail_auth as auth_module
    import ingestion.parse as parse_module

    monkeypatch.setattr(auth_module, "get_gmail_service", lambda allow_interactive=False: object())
    monkeypatch.setattr(fetch_module, "get_message", lambda service, email_id: {"id": email_id})
    monkeypatch.setattr(parse_module, "to_raw_email", lambda message: _raw_email(message["id"]))


def test_refresh_one_never_requests_the_expand_stage(tmp_path, monkeypatch):
    """The regression this guards: adding the auto-expand pipeline stage
    made a previously-unread email's live refresh silently run a second,
    synchronous LLM call (outline, then expand) — doubling the wait behind
    the extension's "Generating reply outline..." message. refresh_one must
    strip "expand" from whatever incremental.stages_for plans, even though
    a read-status flip legitimately plans ("outline", "expand")."""
    db_path = tmp_path / "t.db"
    existing = ProcessedEmail(
        email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
        subject="Hi", received_at=NOW, read_status=ReadStatus.UNREAD,
    )
    persist.upsert([existing], db_path)

    seen_stages = {}

    class _FakePipeline:
        def __init__(self, stages):
            self.stages = stages
            self.errors = []

        def process(self, raws, existing=None):
            return [
                ProcessedEmail(
                    email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
                    subject="Hi", received_at=NOW, read_status=ReadStatus.READ,
                    reply_outline=["Confirm the figures"],
                    reply_outline_status=ReplyOutlineStatus.SUGGESTED,
                    proposed_event_status=ProposedEventStatus.NONE,
                    processed_at=NOW,
                )
            ]

    def fake_with_defaults(cls, stages=None, **kwargs):
        seen_stages["stages"] = stages
        return _FakePipeline(stages)

    monkeypatch.setattr(Pipeline, "with_defaults", classmethod(fake_with_defaults))

    result = refresh.refresh_one("e1", db_path)

    assert "expand" not in seen_stages["stages"]
    assert "outline" in seen_stages["stages"]
    assert result.reply_outline == ["Confirm the figures"]
    assert result.reply_draft is None  # left for the next background poll


def test_refresh_one_skips_the_pipeline_entirely_when_nothing_is_due(tmp_path, monkeypatch):
    """An already-complete, already-read email must cost zero LLM calls on
    a live refresh — not even to discover expand was the only due stage."""
    db_path = tmp_path / "t.db"
    complete = ProcessedEmail(
        email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
        subject="Hi", received_at=NOW, read_status=ReadStatus.READ,
        is_no_reply=False, importance_score=50.0, summary="s", mentioned_dates=[],
        category="c", is_scheduling_related=False,
        reply_outline=["bullet"], reply_outline_status=ReplyOutlineStatus.SUGGESTED,
        reply_draft="Already expanded.", processed_at=NOW,
        context_processed_at=NOW,
    )
    persist.upsert([complete], db_path)

    called = []
    monkeypatch.setattr(
        Pipeline, "with_defaults", classmethod(lambda cls, stages=None, **kw: called.append(stages))
    )

    result = refresh.refresh_one("e1", db_path)

    assert called == []
    assert result.reply_draft == "Already expanded."
