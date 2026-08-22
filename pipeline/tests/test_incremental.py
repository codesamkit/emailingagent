"""Incremental re-run: a changed email must not cost a whole-inbox reprocess."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models.schema import (
    ImportanceLevel,
    ProcessedEmail,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)
from pipeline import incremental
from pipeline.orchestrate import STAGES

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def raw(email_id="e1", read_status=ReadStatus.UNREAD) -> RawEmail:
    return RawEmail(
        email_id=email_id, thread_id="t1", sender="a@b.example",
        recipients=["me@example.com"], subject="Hi", body="Body",
        received_at=NOW, read_status=read_status,
    )


def complete(email_id="e1", read_status=ReadStatus.UNREAD, **overrides) -> ProcessedEmail:
    """A fully processed record — every stage has run."""
    defaults = dict(
        email_id=email_id, thread_id="t1", sender="a@b.example", subject="Hi",
        received_at=NOW, read_status=read_status,
        is_no_reply=False, no_reply_reason="personal sender",
        importance_score=60.0, importance_level=ImportanceLevel.MEDIUM,
        importance_justification="direct ask",
        summary="A short summary.",
        is_scheduling_related=False,
        reply_outline=None,
        reply_outline_status=ReplyOutlineStatus.NONE,
        processed_at=NOW,
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


class TestNothingToDo:
    def test_unchanged_complete_record_needs_no_stages(self):
        """The whole point: a no-op re-run costs zero LLM calls."""
        assert incremental.stages_for(raw(), complete()) == ()

    def test_plan_skips_up_to_date_emails(self):
        raws = [raw("e1"), raw("e2"), raw("e3")]
        existing = {r.email_id: complete(r.email_id) for r in raws}
        assert incremental.plan(raws, existing) == {}

    def test_no_reply_email_is_not_retried_forever(self):
        """A no-reply email has no outline by design — that is not 'missing'."""
        record = complete(
            read_status=ReadStatus.READ,
            is_no_reply=True,
            reply_outline_status=ReplyOutlineStatus.NOT_APPLICABLE,
        )
        assert incremental.stages_for(raw(read_status=ReadStatus.READ), record) == ()

    def test_unread_email_is_not_retried_for_its_missing_outline(self):
        assert incremental.stages_for(raw(), complete()) == ()


class TestFirstRun:
    def test_never_processed_runs_every_stage(self):
        assert incremental.stages_for(raw(), None) == tuple(STAGES)

    def test_processed_at_none_runs_every_stage(self):
        assert incremental.stages_for(raw(), complete(processed_at=None)) == tuple(STAGES)


class TestReadStatusFlip:
    def test_becoming_read_reruns_only_the_outline(self):
        """The headline case. Classification, score, and summary do not depend
        on read status, so re-running them would be pure waste."""
        was_unread = complete(read_status=ReadStatus.UNREAD)
        now_read = raw(read_status=ReadStatus.READ)
        assert incremental.stages_for(now_read, was_unread) == ("outline",)

    def test_flip_does_not_touch_other_emails(self):
        raws = [raw("e1", ReadStatus.READ), raw("e2"), raw("e3")]
        existing = {
            "e1": complete("e1", ReadStatus.UNREAD),
            "e2": complete("e2"),
            "e3": complete("e3"),
        }
        plan = incremental.plan(raws, existing)
        assert plan == {"e1": ("outline",)}

    def test_becoming_unread_again_also_reruns_outline(self):
        was_read = complete(read_status=ReadStatus.READ,
                            reply_outline=["a"], reply_outline_status=ReplyOutlineStatus.SUGGESTED)
        assert incremental.stages_for(raw(read_status=ReadStatus.UNREAD), was_read) == ("outline",)


class TestPartialRecords:
    def test_missing_summary_reruns_only_summarize(self):
        assert incremental.stages_for(raw(), complete(summary=None)) == ("summarize",)

    def test_missing_score_reruns_only_score(self):
        assert incremental.stages_for(raw(), complete(importance_score=None)) == ("score",)

    def test_missing_classification_reruns_only_classify(self):
        assert incremental.stages_for(raw(), complete(is_no_reply=None)) == ("classify",)

    def test_several_missing_fields_run_in_canonical_order(self):
        stages = incremental.stages_for(
            raw(), complete(summary=None, importance_score=None, is_no_reply=None)
        )
        assert stages == ("classify", "score", "summarize")

    def test_eligible_email_missing_its_outline_is_retried(self):
        record = complete(read_status=ReadStatus.READ, is_no_reply=False, reply_outline=None)
        assert "outline" in incremental.stages_for(raw(read_status=ReadStatus.READ), record)

    def test_scheduling_email_without_context_refetches_calendar(self):
        record = complete(is_scheduling_related=True, calendar_context=None)
        assert "calendar" in incremental.stages_for(raw(), record)


class TestSummarizePlan:
    def test_reports_nothing_to_do(self):
        assert "Nothing to do" in incremental.summarize_plan({}, 100)

    def test_counts_stages(self):
        message = incremental.summarize_plan({"e1": ("outline",), "e2": ("outline", "score")}, 100)
        assert "2/100" in message
        assert "outlinex2" in message
        assert "scorex1" in message
