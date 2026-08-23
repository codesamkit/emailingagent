"""Tests for the feedback loop — sender priors recorded, aggregated, applied."""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus, ReplyOutlineStatus
from scoring.score import LEVEL_BANDS, _level_from_score
from feedback import store
from feedback.apply import apply_feedback


def processed(email_id="e1", sender="Bot <bot@shop.com>", is_no_reply=None,
              level=None, score=None, outline=None,
              outline_status=ReplyOutlineStatus.NONE) -> ProcessedEmail:
    return ProcessedEmail(
        email_id=email_id,
        thread_id="t1",
        sender=sender,
        subject="s",
        received_at=datetime(2026, 8, 20, 9, 0, 0),
        read_status=ReadStatus.READ,
        is_no_reply=is_no_reply,
        importance_level=level,
        importance_score=score,
        reply_outline=outline,
        reply_outline_status=outline_status,
    )


class FeedbackDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "test.db"

    def tearDown(self):
        self._tmp.cleanup()


class TestStore(FeedbackDbTest):
    def test_latest_correction_wins_per_sender_and_kind(self):
        store.record("Bot <bot@shop.com>", store.KIND_LEVEL, "high", db_path=self.db)
        store.record("bot@shop.com", store.KIND_LEVEL, "low", db_path=self.db)
        store.record("bot@shop.com", store.KIND_NO_REPLY, "true", db_path=self.db)

        priors = store.sender_priors(self.db)

        self.assertEqual(priors["bot@shop.com"],
                         {"level": ImportanceLevel.LOW, "is_no_reply": True})

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            store.record("a@b.com", "levelz", "low", db_path=self.db)
        with self.assertRaises(ValueError):
            store.record("a@b.com", store.KIND_LEVEL, "megaurgent", db_path=self.db)
        with self.assertRaises(ValueError):
            store.record("a@b.com", store.KIND_NO_REPLY, "yes", db_path=self.db)


class TestApply(FeedbackDbTest):
    def test_level_prior_overrides_and_stays_in_band(self):
        store.record("bot@shop.com", store.KIND_LEVEL, "low", db_path=self.db)
        email = processed(level=ImportanceLevel.HIGH, score=60.0, is_no_reply=False)

        changed = apply_feedback([email], db_path=self.db)

        self.assertEqual(changed, [email])
        self.assertEqual(email.importance_level, ImportanceLevel.LOW)
        self.assertEqual(_level_from_score(email.importance_score), ImportanceLevel.LOW)
        self.assertIn("your feedback", email.importance_justification.lower())

    def test_no_reply_prior_strips_outline(self):
        store.record("bot@shop.com", store.KIND_NO_REPLY, "true", db_path=self.db)
        email = processed(is_no_reply=False, outline=["a"],
                          outline_status=ReplyOutlineStatus.SUGGESTED)

        apply_feedback([email], db_path=self.db)

        self.assertTrue(email.is_no_reply)
        self.assertIsNone(email.reply_outline)
        self.assertEqual(email.reply_outline_status, ReplyOutlineStatus.NOT_APPLICABLE)

    def test_personal_prior_reopens_outline_eligibility(self):
        store.record("human@corp.com", store.KIND_NO_REPLY, "false", db_path=self.db)
        email = processed(sender="human@corp.com", is_no_reply=True,
                          outline_status=ReplyOutlineStatus.NOT_APPLICABLE)

        apply_feedback([email], db_path=self.db)

        self.assertFalse(email.is_no_reply)
        self.assertEqual(email.reply_outline_status, ReplyOutlineStatus.NONE)

    def test_untouched_senders_and_matching_rows_are_skipped(self):
        store.record("bot@shop.com", store.KIND_LEVEL, "low", db_path=self.db)
        other = processed(email_id="e2", sender="human@corp.com",
                          level=ImportanceLevel.HIGH, score=60.0)
        already = processed(email_id="e3", level=ImportanceLevel.LOW, score=5.0)

        changed = apply_feedback([other, already], db_path=self.db)

        self.assertEqual(changed, [])
        self.assertEqual(other.importance_level, ImportanceLevel.HIGH)


if __name__ == "__main__":
    unittest.main()


class TestClear(FeedbackDbTest):
    """Undo. Recording is append-only and latest-wins, so before clear()
    existed a stray click stuck to every email from that sender for good."""

    def test_forgets_every_kind_for_a_sender(self):
        store.record("Bot <bot@shop.com>", store.KIND_LEVEL, "high", db_path=self.db)
        store.record("Bot <bot@shop.com>", store.KIND_NO_REPLY, "true", db_path=self.db)

        removed = store.clear("bot@shop.com", db_path=self.db)

        self.assertEqual(removed, 2)
        self.assertEqual(store.sender_priors(self.db), {})

    def test_can_forget_one_kind_and_keep_the_other(self):
        store.record("bot@shop.com", store.KIND_LEVEL, "high", db_path=self.db)
        store.record("bot@shop.com", store.KIND_NO_REPLY, "true", db_path=self.db)

        removed = store.clear("bot@shop.com", store.KIND_LEVEL, db_path=self.db)

        self.assertEqual(removed, 1)
        self.assertEqual(
            store.sender_priors(self.db), {"bot@shop.com": {"is_no_reply": True}}
        )

    def test_normalizes_the_sender_like_record_does(self):
        """record() stores the bare address; clear() must find it whether the
        caller passes a bare address or a full 'Name <addr>' header."""
        store.record("bot@shop.com", store.KIND_LEVEL, "low", db_path=self.db)

        self.assertEqual(store.clear("Bot <bot@shop.com>", db_path=self.db), 1)

    def test_clearing_an_unknown_sender_is_a_no_op(self):
        self.assertEqual(store.clear("nobody@nowhere.com", db_path=self.db), 0)

    def test_rejects_an_unknown_kind(self):
        with self.assertRaises(ValueError):
            store.clear("bot@shop.com", "nonsense", db_path=self.db)

    def test_a_cleared_prior_stops_being_applied(self):
        """The end-to-end point of the undo: apply_feedback must stop
        overriding once the correction is forgotten."""
        store.record("bot@shop.com", store.KIND_LEVEL, "urgent", db_path=self.db)
        email = processed(level=ImportanceLevel.LOW, score=10.0)
        self.assertEqual(len(apply_feedback([email], db_path=self.db)), 1)
        self.assertEqual(email.importance_level, ImportanceLevel.URGENT)

        store.clear("bot@shop.com", db_path=self.db)

        untouched = processed(level=ImportanceLevel.LOW, score=10.0)
        self.assertEqual(apply_feedback([untouched], db_path=self.db), [])
        self.assertEqual(untouched.importance_level, ImportanceLevel.LOW)
