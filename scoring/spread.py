"""Rank-based score spreading within each importance band.

Any per-email scorer — model or rules — produces clustered raw scores,
because similar emails carry similar signals; the last measured run put
every email between 20 and 34 with big ties at exactly 0.0 and 25.0.
Ranking needs *separation*, and separation is a property of the whole
inbox, not of one email, so no single-email prompt or weight tweak can
provide it. This pass runs after scoring: within each importance level it
re-maps scores to an even distribution across that level's band,
preserving the order the raw scores (and recency, as tiebreak) already
established.

The level never changes, and scores stay strictly inside their level's
band, so `_level_from_score(score) == level` still holds afterwards. The
mapping is idempotent: spreading already-spread scores is a no-op.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from models.schema import ImportanceLevel, ProcessedEmail
from scoring.score import LEVEL_BANDS
from scoring.signals import _as_utc


def spread_scores(emails: Iterable[ProcessedEmail]) -> List[ProcessedEmail]:
    """Re-map importance_score so each level's emails sit evenly across
    that level's band. Returns the (mutated) scored emails; rows without a
    score or level are left untouched and excluded from the result."""

    by_level: Dict[ImportanceLevel, List[ProcessedEmail]] = {}
    for email in emails:
        if email.importance_score is None or email.importance_level is None:
            continue
        by_level.setdefault(email.importance_level, []).append(email)

    spreaded: List[ProcessedEmail] = []
    for level, group in by_level.items():
        floor, ceiling = LEVEL_BANDS[level]
        span = ceiling - floor
        # Raw score carries the scorer's ordering; recency breaks ties so
        # identical raw scores (e.g. a band-floor pin) still rank stably.
        group.sort(key=lambda e: (e.importance_score, _as_utc(e.received_at)))
        n = len(group)
        for rank, email in enumerate(group, start=1):
            # (rank)/(n+1) keeps every value strictly inside the band, so
            # the top of one band can never touch the next band's floor.
            email.importance_score = round(floor + span * rank / (n + 1), 1)
        spreaded.extend(group)
    return spreaded
