"""Tests for scoring/spread.py — rank-based within-band score spreading."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus
from scoring.score import LEVEL_BANDS, _level_from_score
from scoring.spread import spread_scores


def processed(email_id: str, score: float | None, level: ImportanceLevel | None,
              received_at: datetime | None = None) -> ProcessedEmail:
    return ProcessedEmail(
        email_id=email_id,
        thread_id="t1",
        sender="a@b.com",
        subject="s",
        received_at=received_at or datetime(2026, 8, 20, 9, 0, 0),
        read_status=ReadStatus.READ,
        importance_score=score,
        importance_level=level,
    )


class TestSpreadScores(unittest.TestCase):
    def test_ties_become_distinct_and_evenly_spaced(self):
        base = datetime(2026, 8, 20, 9, 0, 0)
        group = [processed(f"e{i}", 25.0, ImportanceLevel.MEDIUM,
                           base + timedelta(hours=i)) for i in range(4)]

        spread_scores(group)

        scores = sorted(e.importance_score for e in group)
        self.assertEqual(len(set(scores)), 4)
        gaps = [round(b - a, 1) for a, b in zip(scores, scores[1:])]
        self.assertEqual(len(set(gaps)), 1)  # even spacing

    def test_order_is_preserved(self):
        low = processed("low", 26.0, ImportanceLevel.MEDIUM)
        high = processed("high", 34.0, ImportanceLevel.MEDIUM)

        spread_scores([low, high])

        self.assertGreater(high.importance_score, low.importance_score)

    def test_scores_stay_inside_their_band(self):
        for level in ImportanceLevel:
            floor, ceiling = LEVEL_BANDS[level]
            group = [processed(f"{level.value}{i}", floor, level,
                               datetime(2026, 8, 20, 9, i))
                     for i in range(5)]
            spread_scores(group)
            for email in group:
                self.assertEqual(_level_from_score(email.importance_score), level)
                self.assertGreater(email.importance_score, floor - 0.01)
                self.assertLess(email.importance_score, ceiling)

    def test_idempotent(self):
        base = datetime(2026, 8, 20, 9, 0, 0)
        group = [processed(f"e{i}", 25.0 + i, ImportanceLevel.MEDIUM,
                           base + timedelta(hours=i)) for i in range(5)]

        spread_scores(group)
        first = {e.email_id: e.importance_score for e in group}
        spread_scores(group)

        self.assertEqual(first, {e.email_id: e.importance_score for e in group})

    def test_unscored_rows_are_left_alone(self):
        unscored = processed("u", None, None)
        result = spread_scores([unscored, processed("s", 25.0, ImportanceLevel.MEDIUM)])

        self.assertIsNone(unscored.importance_score)
        self.assertEqual([e.email_id for e in result], ["s"])


if __name__ == "__main__":
    unittest.main()
