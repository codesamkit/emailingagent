"""End-to-end pipeline behavior, with every stage faked — no LLM, no network."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from models.schema import (
    CalendarContext,
    CalendarSlot,
    ImportanceLevel,
    ProcessedEmail,
    ProposedEvent,
    ProposedEventStatus,
    RawEmail,
    ReadStatus,
    ReplyOutlineStatus,
)
from pipeline import persist
from pipeline.orchestrate import Pipeline, to_processed

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def raw(email_id="e1", read_status=ReadStatus.READ, subject="Hi") -> RawEmail:
    return RawEmail(
        email_id=email_id, thread_id="t1", sender="Dana <dana@example.com>",
        recipients=["me@example.com"], subject=subject, body="Body text",
        received_at=NOW, read_status=read_status,
    )


def full_pipeline(**overrides) -> Pipeline:
    defaults = dict(
        classify=lambda e: (False, "personal sender"),
        score=lambda e, nr: (72.5, ImportanceLevel.HIGH, "direct ask"),
        summarize=lambda e: ("Dana needs the Q3 figures.", ["Friday"]),
        categorize=lambda e: "team planning",
        scheduling_gate=lambda e: False,
        calendar_context=lambda s, t: CalendarContext(range_start=s, range_end=t),
        outline=lambda p, r: (["Confirm the figures"], ReplyOutlineStatus.SUGGESTED),
    )
    defaults.update(overrides)
    return Pipeline(**defaults)


class TestHappyPath:
    def test_every_field_is_populated(self):
        result = full_pipeline().process_one(raw(), now=NOW)
        assert result.is_no_reply is False
        assert result.no_reply_reason == "personal sender"
        assert result.importance_score == 72.5
        assert result.importance_level == ImportanceLevel.HIGH
        assert result.summary == "Dana needs the Q3 figures."
        assert result.mentioned_dates == ["Friday"]
        assert result.category == "team planning"
        assert result.is_scheduling_related is False
        assert result.reply_outline == ["Confirm the figures"]
        assert result.reply_outline_status == ReplyOutlineStatus.SUGGESTED
        assert result.processed_at == NOW

    def test_identity_fields_copy_through_unchanged(self):
        result = full_pipeline().process_one(raw(), now=NOW)
        assert (result.email_id, result.thread_id, result.sender) == (
            "e1", "t1", "Dana <dana@example.com>")

    def test_batch_processes_every_email(self):
        results = full_pipeline().process([raw("a"), raw("b"), raw("c")], now=NOW)
        assert [r.email_id for r in results] == ["a", "b", "c"]

    def test_progress_callback_is_invoked(self):
        seen = []
        full_pipeline().process([raw("a"), raw("b")], now=NOW,
                                on_progress=lambda d, t: seen.append((d, t)))
        assert seen == [(1, 2), (2, 2)]


class TestStageOrdering:
    def test_classification_result_reaches_scoring(self):
        """score_importance takes is_no_reply — it must be the fresh value."""
        seen = {}

        def score(email, is_no_reply):
            seen["is_no_reply"] = is_no_reply
            return (10.0, ImportanceLevel.LOW, "bulk")

        full_pipeline(classify=lambda e: (True, "no-reply@"), score=score).process_one(
            raw(), now=NOW)
        assert seen["is_no_reply"] is True

    def test_outline_sees_the_populated_record(self):
        """The gate reads is_no_reply, so classification must run first."""
        seen = {}

        def outline(processed, raw_email):
            seen["is_no_reply"] = processed.is_no_reply
            seen["summary"] = processed.summary
            return (None, ReplyOutlineStatus.NOT_APPLICABLE)

        full_pipeline(classify=lambda e: (True, "bulk"), outline=outline).process_one(
            raw(), now=NOW)
        assert seen["is_no_reply"] is True
        assert seen["summary"] == "Dana needs the Q3 figures."


class TestExpandStage:
    def test_expand_runs_automatically_after_a_suggested_outline(self):
        result = full_pipeline(
            expand=lambda email_id, outline: "Hi Dana,\n\nConfirmed.\n\nBest,",
        ).process_one(raw(), now=NOW)
        assert result.reply_draft == "Hi Dana,\n\nConfirmed.\n\nBest,"

    def test_expand_sees_the_generated_outline(self):
        seen = {}

        def expand(email_id, outline):
            seen["email_id"] = email_id
            seen["outline"] = outline
            return "draft"

        full_pipeline(expand=expand).process_one(raw(), now=NOW)
        assert seen == {"email_id": "e1", "outline": ["Confirm the figures"]}

    def test_expand_does_not_run_when_the_email_has_no_outline(self):
        """Unread/no-reply emails never get an outline, so expand must not
        even be attempted — same gate the outline stage itself enforces."""
        calls = []
        full_pipeline(
            outline=lambda p, r: (None, ReplyOutlineStatus.NOT_APPLICABLE),
            expand=lambda email_id, outline: calls.append(1) or "draft",
        ).process_one(raw(), now=NOW)
        assert calls == []

    def test_expand_does_not_rerun_once_a_draft_already_exists(self):
        """Zero-cost re-run: an email that already has a draft must not pay
        for another LLM call just because the pipeline ran again."""
        calls = []
        existing = ProcessedEmail(
            email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
            subject="Hi", received_at=NOW, read_status=ReadStatus.READ,
            reply_outline=["Confirm the figures"],
            reply_outline_status=ReplyOutlineStatus.SUGGESTED,
            reply_draft="Already expanded.",
        )
        result = full_pipeline(
            expand=lambda email_id, outline: calls.append(1) or "new draft",
        ).process_one(raw(), existing=existing, now=NOW)
        assert calls == []
        assert result.reply_draft == "Already expanded."

    def test_expand_failure_leaves_the_draft_unset_without_aborting(self):
        def boom(email_id, outline):
            raise RuntimeError("API timeout")

        result = full_pipeline(expand=boom).process_one(raw(), now=NOW)
        assert result.reply_draft is None
        assert result.reply_outline == ["Confirm the figures"]  # unaffected


class TestCalendarGate:
    def test_calendar_is_not_fetched_for_non_scheduling_email(self):
        """The whole reason the cheap gate exists."""
        calls = []
        full_pipeline(
            scheduling_gate=lambda e: False,
            calendar_context=lambda s, t: calls.append(1) or CalendarContext(s, t),
        ).process_one(raw(), now=NOW)
        assert calls == []

    def test_calendar_is_fetched_for_a_scheduling_email(self):
        calls = []
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            calendar_context=lambda s, t: (calls.append(1), CalendarContext(s, t))[1],
        ).process_one(raw(), now=NOW)
        assert calls == [1]
        assert result.calendar_context is not None

    def test_one_calendar_window_is_shared_across_a_batch(self):
        """Ten scheduling emails must not mean ten freebusy queries."""
        calls = []
        pipeline = full_pipeline(
            scheduling_gate=lambda e: True,
            calendar_context=lambda s, t: (calls.append(1), CalendarContext(s, t))[1],
        )
        pipeline.process([raw("a"), raw("b"), raw("c")], now=NOW)
        assert len(calls) == 1


class TestProposedEventStage:
    def test_not_called_for_non_scheduling_email(self):
        calls = []
        full_pipeline(
            scheduling_gate=lambda e: False,
            propose_event=lambda p, r: calls.append(1) or (None, ProposedEventStatus.NONE),
        ).process_one(raw(), now=NOW)
        assert calls == []

    def test_called_for_a_scheduling_email(self):
        proposed = ProposedEvent(title="Sync", start=NOW, end=NOW + timedelta(minutes=30))
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: (proposed, ProposedEventStatus.SUGGESTED),
        ).process_one(raw(), now=NOW)
        assert result.proposed_event is proposed
        assert result.proposed_event_status == ProposedEventStatus.SUGGESTED

    def test_approved_status_is_never_overwritten_on_rerun(self):
        """Once a user has approved, a real calendar event may exist — a
        later pipeline run must not regenerate or overwrite it."""
        old_event = ProposedEvent(
            title="Original", start=NOW, end=NOW + timedelta(minutes=30),
            google_event_id="abc123",
        )
        existing = ProcessedEmail(
            email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
            subject="Hi", received_at=NOW, read_status=ReadStatus.READ,
            is_scheduling_related=True,
            proposed_event=old_event, proposed_event_status=ProposedEventStatus.APPROVED,
        )
        calls = []
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: calls.append(1) or (
                ProposedEvent(title="New", start=NOW, end=NOW + timedelta(minutes=30)),
                ProposedEventStatus.SUGGESTED,
            ),
        ).process_one(raw(), existing=existing, now=NOW)
        assert calls == []
        assert result.proposed_event is old_event
        assert result.proposed_event_status == ProposedEventStatus.APPROVED

    def test_declined_status_is_never_overwritten_on_rerun(self):
        existing = ProcessedEmail(
            email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
            subject="Hi", received_at=NOW, read_status=ReadStatus.READ,
            is_scheduling_related=True,
            proposed_event_status=ProposedEventStatus.DECLINED,
        )
        calls = []
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: calls.append(1) or (None, ProposedEventStatus.NONE),
        ).process_one(raw(), existing=existing, now=NOW)
        assert calls == []
        assert result.proposed_event_status == ProposedEventStatus.DECLINED


class TestGracefulDegradation:
    def test_one_stage_failure_does_not_abort_the_email(self):
        def boom(email):
            raise RuntimeError("API timeout")

        result = full_pipeline(summarize=boom).process_one(raw(), now=NOW)
        assert result.summary is None            # the failed stage
        assert result.importance_score == 72.5   # everything else survived
        assert result.reply_outline == ["Confirm the figures"]

    def test_categorize_failure_leaves_category_unset(self):
        def boom(email):
            raise RuntimeError("API timeout")

        result = full_pipeline(categorize=boom).process_one(raw(), now=NOW)
        assert result.category is None           # retried next run
        assert result.summary == "Dana needs the Q3 figures."
        assert result.reply_outline == ["Confirm the figures"]

    def test_one_email_failure_does_not_abort_the_batch(self):
        def flaky(email):
            if email.email_id == "b":
                raise RuntimeError("boom")
            return ("ok", [])

        results = full_pipeline(summarize=flaky).process(
            [raw("a"), raw("b"), raw("c")], now=NOW)
        assert [r.summary for r in results] == ["ok", None, "ok"]

    def test_failures_are_collected_for_reporting(self):
        def boom(email):
            raise RuntimeError("API timeout")

        pipeline = full_pipeline(summarize=boom)
        pipeline.process([raw("a")], now=NOW)
        assert len(pipeline.errors) == 1
        assert "summarize failed for a" in pipeline.errors[0]

    def test_calendar_failure_leaves_the_rest_intact(self):
        def boom(start, end):
            raise RuntimeError("calendar down")

        result = full_pipeline(
            scheduling_gate=lambda e: True, calendar_context=boom
        ).process_one(raw(), now=NOW)
        assert result.calendar_context is None
        assert result.is_scheduling_related is True
        assert result.summary is not None


class TestPartialStages:
    def test_only_the_enabled_stages_run(self):
        called = []
        pipeline = full_pipeline(
            classify=lambda e: called.append("classify") or (False, "x"),
            summarize=lambda e: called.append("summarize") or ("s", []),
            stages=("summarize",),
        )
        pipeline.process_one(raw(), now=NOW)
        assert called == ["summarize"]

    def test_existing_results_are_carried_forward(self):
        """A summarize-only re-run must not blank the score."""
        existing = ProcessedEmail(
            email_id="e1", thread_id="t1", sender="Dana <dana@example.com>",
            subject="Hi", received_at=NOW, read_status=ReadStatus.READ,
            is_no_reply=False, importance_score=88.0,
            importance_level=ImportanceLevel.URGENT, summary="old",
        )
        pipeline = full_pipeline(stages=("summarize",))
        result = pipeline.process_one(raw(), existing=existing, now=NOW)
        assert result.summary == "Dana needs the Q3 figures."  # refreshed
        assert result.importance_score == 88.0                  # preserved

    def test_read_status_is_always_refreshed_even_when_its_stage_is_off(self):
        """Read status is what makes an email become outline-eligible."""
        existing = ProcessedEmail(
            email_id="e1", thread_id="t1", sender="a", subject="Hi",
            received_at=NOW, read_status=ReadStatus.UNREAD,
        )
        result = full_pipeline(stages=("outline",)).process_one(
            raw(read_status=ReadStatus.READ), existing=existing, now=NOW)
        assert result.read_status == ReadStatus.READ


class TestPersistenceRoundTrip:
    def test_full_record_survives_a_round_trip(self, tmp_path):
        db_path = tmp_path / "t.db"
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            calendar_context=lambda s, t: CalendarContext(
                range_start=s, range_end=t,
                busy_blocks=[CalendarSlot(s, s + timedelta(hours=1))],
                suggested_slots=[CalendarSlot(s + timedelta(hours=2), s + timedelta(hours=3))],
                existing_events=[{"summary": "Standup", "start": s, "all_day": False}],
            ),
            expand=lambda email_id, outline: "Hi Dana,\n\nConfirmed.\n\nBest,",
        ).process_one(raw(), now=NOW)

        persist.upsert([result], db_path)
        back = persist.get("e1", db_path)

        assert back.importance_score == result.importance_score
        assert back.importance_level == ImportanceLevel.HIGH
        assert back.summary == result.summary
        assert back.mentioned_dates == ["Friday"]
        assert back.category == "team planning"
        assert back.reply_outline == ["Confirm the figures"]
        assert back.reply_outline_status == ReplyOutlineStatus.SUGGESTED
        assert back.reply_draft == "Hi Dana,\n\nConfirmed.\n\nBest,"
        assert back.read_status == ReadStatus.READ
        assert back.processed_at == NOW
        assert len(back.calendar_context.busy_blocks) == 1
        assert len(back.calendar_context.suggested_slots) == 1
        assert back.calendar_context.existing_events[0]["summary"] == "Standup"
        assert isinstance(back.calendar_context.existing_events[0]["start"], datetime)

    def test_nulls_round_trip_as_none_not_as_false(self):
        """'Not classified yet' must not come back as 'classified as not-no-reply'."""
        import tempfile, pathlib
        db_path = pathlib.Path(tempfile.mkdtemp()) / "t.db"
        record = to_processed(raw())
        persist.upsert([record], db_path)
        back = persist.get("e1", db_path)
        assert back.is_no_reply is None
        assert back.is_scheduling_related is None
        assert back.importance_score is None
        assert back.summary is None
        assert back.mentioned_dates is None
        assert back.category is None

    def test_upsert_refreshes_rather_than_duplicating(self, tmp_path):
        db_path = tmp_path / "t.db"
        first = full_pipeline().process_one(raw(), now=NOW)
        persist.upsert([first], db_path)
        second = full_pipeline(summarize=lambda e: ("updated", [])).process_one(raw(), now=NOW)
        persist.upsert([second], db_path)
        assert persist.count(db_path) == 1
        assert persist.get("e1", db_path).summary == "updated"

    def test_unscored_rows_sort_last_not_first(self, tmp_path):
        """SQLite puts NULL first under DESC — that would top the review list
        with unprocessed mail."""
        db_path = tmp_path / "t.db"
        scored = full_pipeline().process_one(raw("scored"), now=NOW)
        unscored = to_processed(raw("unscored"))
        persist.upsert([unscored, scored], db_path)
        assert [e.email_id for e in persist.all_processed(db_path)] == ["scored", "unscored"]

    def test_update_outline_persists_user_edits(self, tmp_path):
        db_path = tmp_path / "t.db"
        persist.upsert([full_pipeline().process_one(raw(), now=NOW)], db_path)
        assert persist.update_outline("e1", ["edited bullet"], ReplyOutlineStatus.EDITED, db_path)
        back = persist.get("e1", db_path)
        assert back.reply_outline == ["edited bullet"]
        assert back.reply_outline_status == ReplyOutlineStatus.EDITED

    def test_update_outline_reports_unknown_email(self, tmp_path):
        assert not persist.update_outline("nope", ["x"], ReplyOutlineStatus.EDITED, tmp_path / "t.db")

    def test_update_draft_persists_a_regenerated_draft(self, tmp_path):
        db_path = tmp_path / "t.db"
        persist.upsert([full_pipeline().process_one(raw(), now=NOW)], db_path)
        assert persist.update_draft("e1", "Regenerated draft.", db_path)
        back = persist.get("e1", db_path)
        assert back.reply_draft == "Regenerated draft."

    def test_update_draft_reports_unknown_email(self, tmp_path):
        assert not persist.update_draft("nope", "x", tmp_path / "t.db")

    def test_proposed_event_survives_a_round_trip(self, tmp_path):
        db_path = tmp_path / "t.db"
        proposed = ProposedEvent(
            title="Sync with Dana", start=NOW, end=NOW + timedelta(minutes=30),
            attendees=["dana@example.com"], location="Room 2",
        )
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: (proposed, ProposedEventStatus.SUGGESTED),
        ).process_one(raw(), now=NOW)

        persist.upsert([result], db_path)
        back = persist.get("e1", db_path)

        assert back.proposed_event_status == ProposedEventStatus.SUGGESTED
        assert back.proposed_event.title == "Sync with Dana"
        assert back.proposed_event.start == NOW
        assert back.proposed_event.attendees == ["dana@example.com"]
        assert back.proposed_event.location == "Room 2"

    def test_update_proposed_event_status_persists_approval(self, tmp_path):
        db_path = tmp_path / "t.db"
        proposed = ProposedEvent(title="Sync", start=NOW, end=NOW + timedelta(minutes=30))
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: (proposed, ProposedEventStatus.SUGGESTED),
        ).process_one(raw(), now=NOW)
        persist.upsert([result], db_path)

        approved = ProposedEvent(
            title="Sync", start=NOW, end=NOW + timedelta(minutes=30),
            google_event_id="gcal-123",
        )
        assert persist.update_proposed_event_status(
            "e1", ProposedEventStatus.APPROVED, approved, db_path
        )
        back = persist.get("e1", db_path)
        assert back.proposed_event_status == ProposedEventStatus.APPROVED
        assert back.proposed_event.google_event_id == "gcal-123"

    def test_update_proposed_event_status_reports_unknown_email(self, tmp_path):
        assert not persist.update_proposed_event_status(
            "nope", ProposedEventStatus.DECLINED, db_path=tmp_path / "t.db"
        )


class TestAutoAdd:
    """`CALENDAR_AUTO_ADD` makes the pipeline create the event itself instead
    of parking it as SUGGESTED for a human click. The creator is injected like
    every other stage callable, so a pipeline without one never reaches
    Calendar -- without that, running the suite writes real events to a real
    calendar.
    """

    def _proposed(self, start=None, end=None):
        start = start if start is not None else NOW + timedelta(days=1)
        return ProposedEvent(
            title="Sync", start=start, end=end if end is not None else start + timedelta(minutes=30)
        )

    def _pipeline(self, proposed, creator):
        return full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: (proposed, ProposedEventStatus.SUGGESTED),
            create_event=creator,
        )

    def test_no_creator_injected_means_no_calendar_call(self):
        result = full_pipeline(
            scheduling_gate=lambda e: True,
            propose_event=lambda p, r: (self._proposed(), ProposedEventStatus.SUGGESTED),
        ).process_one(raw(), now=NOW)
        assert result.proposed_event_status == ProposedEventStatus.SUGGESTED

    def test_auto_adds_and_marks_approved(self):
        calls = []

        def creator(event):
            calls.append(event)
            return replace(event, google_event_id="gcal-1")

        result = self._pipeline(self._proposed(), creator).process_one(raw(), now=NOW)
        assert len(calls) == 1
        assert result.proposed_event_status == ProposedEventStatus.APPROVED
        assert result.proposed_event.google_event_id == "gcal-1"

    def test_a_failed_create_is_recorded_as_failed(self):
        def creator(event):
            return replace(event, google_event_id=None, error="quota exceeded")

        result = self._pipeline(self._proposed(), creator).process_one(raw(), now=NOW)
        assert result.proposed_event_status == ProposedEventStatus.FAILED
        assert result.proposed_event.error == "quota exceeded"

    def test_past_events_are_left_for_a_human(self):
        """Extraction still emits dates that have already passed -- 16 of the
        62 proposals in the demo mailbox. Auto-adding those is noise."""
        calls = []
        proposed = self._proposed(start=NOW - timedelta(days=3))
        result = self._pipeline(proposed, lambda e: calls.append(e)).process_one(raw(), now=NOW)
        assert calls == []
        assert result.proposed_event_status == ProposedEventStatus.SUGGESTED

    def test_multi_day_spans_are_left_for_a_human(self):
        """A 13-day "meeting" is an extraction artifact, not a calendar entry."""
        calls = []
        proposed = self._proposed(start=NOW + timedelta(days=1), end=NOW + timedelta(days=14))
        result = self._pipeline(proposed, lambda e: calls.append(e)).process_one(raw(), now=NOW)
        assert calls == []
        assert result.proposed_event_status == ProposedEventStatus.SUGGESTED

    def test_disabled_by_config(self, monkeypatch):
        from calendaring import config as calendar_config

        monkeypatch.setattr(calendar_config, "AUTO_ADD_EVENTS", False)
        calls = []
        result = self._pipeline(self._proposed(), lambda e: calls.append(e)).process_one(
            raw(), now=NOW
        )
        assert calls == []
        assert result.proposed_event_status == ProposedEventStatus.SUGGESTED
