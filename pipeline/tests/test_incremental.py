"""Incremental re-run: a changed email must not cost a whole-inbox reprocess."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models.schema import (
    CalendarContext,
    ImportanceLevel,
    ProcessedEmail,
    ProposedEventStatus,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)
from pipeline import incremental
from pipeline.orchestrate import ALL_STAGE_NAMES

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def raw(email_id="e1", read_status=ReadStatus.UNREAD) -> RawEmail:
    return RawEmail(
        email_id=email_id, thread_id="t1", sender="a@b.example",
        recipients=["me@example.com"], subject="Hi", body="Body",
        received_at=NOW, read_status=read_status,
    )


def complete(email_id="e1", read_status=ReadStatus.UNREAD, **overrides) -> ProcessedEmail:
    """A fully processed record — every stage has run.

    read_status defaults to UNREAD deliberately: drafting eligibility no
    longer depends on it (drafting.outline.is_eligible is read-status-blind),
    so a "complete" record has its outline and draft regardless of whether
    it's been opened.
    """
    defaults = dict(
        email_id=email_id, thread_id="t1", sender="a@b.example", subject="Hi",
        received_at=NOW, read_status=read_status,
        is_no_reply=False, no_reply_reason="personal sender",
        importance_score=60.0, importance_level=ImportanceLevel.MEDIUM,
        importance_justification="direct ask",
        summary="A short summary.",
        mentioned_dates=[],
        action_items=[],
        category="team planning",
        is_scheduling_related=False,
        reply_outline=["Confirm the details"],
        reply_outline_status=ReplyOutlineStatus.SUGGESTED,
        reply_draft="Hi,\n\nConfirmed.\n\nBest,",
        processed_at=NOW,
        context_processed_at=NOW,
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
        assert incremental.stages_for(raw(), None) == tuple(ALL_STAGE_NAMES)

    def test_processed_at_none_runs_every_stage(self):
        assert incremental.stages_for(raw(), complete(processed_at=None)) == tuple(
            ALL_STAGE_NAMES
        )


class TestReadStatusFlip:
    """Drafting eligibility doesn't depend on read status (drafting.outline.
    is_eligible is read-status-blind), so a flip alone — nothing else
    changed — has nothing to trigger: outline/expand already ran when the
    email arrived, whichever way its read status happened to be at the time."""

    def test_becoming_read_reruns_nothing_when_already_complete(self):
        was_unread = complete(read_status=ReadStatus.UNREAD)
        now_read = raw(read_status=ReadStatus.READ)
        assert incremental.stages_for(now_read, was_unread) == ()

    def test_flip_does_not_touch_other_emails(self):
        raws = [raw("e1", ReadStatus.READ), raw("e2"), raw("e3")]
        existing = {
            "e1": complete("e1", ReadStatus.UNREAD),
            "e2": complete("e2"),
            "e3": complete("e3"),
        }
        assert incremental.plan(raws, existing) == {}

    def test_becoming_unread_again_reruns_nothing_either(self):
        was_read = complete(read_status=ReadStatus.READ,
                            reply_outline=["a"], reply_outline_status=ReplyOutlineStatus.SUGGESTED)
        assert incremental.stages_for(raw(read_status=ReadStatus.UNREAD), was_read) == ()


class TestReadStatusFlipDoesNotTouchTheOutline:
    """The opposite of the old invariant: marking an email unread again must
    NOT clear or regenerate its already-generated outline/draft — read
    status carries no meaning for drafting any more."""

    def test_becoming_unread_keeps_an_edited_outline(self):
        from drafting.outline import generate_reply_outline
        from pipeline.orchestrate import Pipeline

        existing = complete(
            read_status=ReadStatus.READ,
            reply_outline=["User's own edited bullet"],
            reply_outline_status=ReplyOutlineStatus.EDITED,
        )
        now_unread = raw(read_status=ReadStatus.UNREAD)

        stages = incremental.stages_for(now_unread, existing)
        pipeline = Pipeline(outline=generate_reply_outline, stages=stages)
        result = pipeline.process_one(now_unread, existing=existing, now=NOW)

        assert result.reply_outline == ["User's own edited bullet"]
        assert result.reply_outline_status == ReplyOutlineStatus.EDITED
        assert result.read_status == ReadStatus.UNREAD

    def test_reprocessing_the_same_flip_twice_is_idempotent(self):
        from drafting.outline import generate_reply_outline
        from pipeline.orchestrate import Pipeline

        existing = complete(
            read_status=ReadStatus.READ,
            reply_outline=["User's own edited bullet"],
            reply_outline_status=ReplyOutlineStatus.EDITED,
        )
        now_unread = raw(read_status=ReadStatus.UNREAD)

        pipeline = Pipeline(outline=generate_reply_outline, stages=incremental.stages_for(now_unread, existing))
        first = pipeline.process_one(now_unread, existing=existing, now=NOW)
        second = pipeline.process_one(now_unread, existing=first, now=NOW)

        assert first.reply_outline == second.reply_outline == ["User's own edited bullet"]
        assert first.reply_outline_status == second.reply_outline_status == ReplyOutlineStatus.EDITED


class TestPartialRecords:
    def test_missing_summary_reruns_only_summarize(self):
        assert incremental.stages_for(raw(), complete(summary=None)) == ("summarize",)

    def test_summary_present_but_dates_never_backfilled_reruns_summarize(self):
        """A row summarized before mentioned_dates existed must not look
        'done' forever just because summary is set."""
        record = complete(mentioned_dates=None)
        assert incremental.stages_for(raw(), record) == ("summarize",)

    def test_missing_score_reruns_only_score(self):
        assert incremental.stages_for(raw(), complete(importance_score=None)) == ("score",)

    def test_missing_classification_reruns_only_classify(self):
        assert incremental.stages_for(raw(), complete(is_no_reply=None)) == ("classify",)

    def test_missing_category_reruns_only_categorize(self):
        """Rows written before the categorize stage existed get backfilled."""
        assert incremental.stages_for(raw(), complete(category=None)) == ("categorize",)

    def test_several_missing_fields_run_in_canonical_order(self):
        stages = incremental.stages_for(
            raw(), complete(summary=None, importance_score=None, is_no_reply=None)
        )
        assert stages == ("classify", "score", "summarize")

    def test_eligible_email_missing_its_outline_is_retried(self):
        record = complete(read_status=ReadStatus.READ, is_no_reply=False, reply_outline=None)
        assert "outline" in incremental.stages_for(raw(read_status=ReadStatus.READ), record)

    def test_unread_but_eligible_email_missing_its_outline_is_also_retried(self):
        """Read status has no bearing on eligibility — an unread real email
        missing its outline is retried exactly like a read one."""
        record = complete(read_status=ReadStatus.UNREAD, is_no_reply=False, reply_outline=None)
        assert "outline" in incremental.stages_for(raw(read_status=ReadStatus.UNREAD), record)

    def test_scheduling_email_without_context_refetches_calendar(self):
        record = complete(is_scheduling_related=True, calendar_context=None)
        assert "calendar" in incremental.stages_for(raw(), record)

    def test_scheduling_email_without_context_also_reruns_propose_event(self):
        """A calendar refetch means propose_event has fresh input too."""
        record = complete(is_scheduling_related=True, calendar_context=None)
        assert "propose_event" in incremental.stages_for(raw(), record)

    def test_scheduling_email_missing_proposed_event_reruns_it(self):
        """A row processed before this feature shipped: calendar_context is
        already there, but extraction never ran (proposed_event_status is
        still the default NONE) — it must not stay stuck that way forever."""
        record = complete(
            is_scheduling_related=True,
            calendar_context=CalendarContext(range_start=NOW, range_end=NOW),
        )
        assert incremental.stages_for(raw(), record) == ("propose_event",)

    def test_scheduling_email_with_pending_proposal_is_not_retried(self):
        record = complete(
            is_scheduling_related=True,
            calendar_context=CalendarContext(range_start=NOW, range_end=NOW),
            proposed_event_status=ProposedEventStatus.SUGGESTED,
        )
        assert incremental.stages_for(raw(), record) == ()

    def test_non_scheduling_email_never_gets_propose_event_scheduled(self):
        record = complete(is_scheduling_related=False)
        assert incremental.stages_for(raw(), record) == ()


class TestContextPass:
    """chunk/embed/extract share one completion marker (context_processed_at)
    and must never be invalidated by a read-status flip — the body content
    didn't change, only whether the user has opened the message."""

    def test_missing_context_pass_reruns_chunk_embed_extract(self):
        record = complete(context_processed_at=None)
        stages = incremental.stages_for(raw(), record)
        assert stages == ("chunk", "embed", "extract")

    def test_context_pass_done_is_not_retried(self):
        assert incremental.stages_for(raw(), complete()) == ()

    def test_read_flip_does_not_rerun_the_context_pass(self):
        was_unread = complete(context_processed_at=NOW, read_status=ReadStatus.UNREAD)
        now_read = raw(read_status=ReadStatus.READ)
        stages = incremental.stages_for(now_read, was_unread)
        assert "chunk" not in stages and "embed" not in stages and "extract" not in stages
        # Nothing else is due either: complete() already carries an outline
        # and draft, and a flip alone no longer invalidates either.
        assert stages == ()

    def test_missing_context_pass_and_read_flip_together(self):
        """The context pass is due because it's missing — not because of the
        flip, which by itself triggers nothing now."""
        was_unread = complete(context_processed_at=None, read_status=ReadStatus.UNREAD)
        now_read = raw(read_status=ReadStatus.READ)
        stages = incremental.stages_for(now_read, was_unread)
        assert stages == ("chunk", "embed", "extract")


class TestSummarizePlan:
    def test_reports_nothing_to_do(self):
        assert "Nothing to do" in incremental.summarize_plan({}, 100)

    def test_counts_stages(self):
        message = incremental.summarize_plan({"e1": ("outline",), "e2": ("outline", "score")}, 100)
        assert "2/100" in message
        assert "outlinex2" in message
        assert "scorex1" in message
