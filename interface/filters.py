"""Filter helpers over a list[ProcessedEmail] for the review interface
(Phase 7, step 5). Pure functions, composable by the CLI's flag parsing -
no dependency on where the list came from (fixtures now, pipeline.persist
once Phase 6 is wired in).
"""

from __future__ import annotations

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus


def by_read_status(emails: list[ProcessedEmail], read_status: ReadStatus) -> list[ProcessedEmail]:
    return [e for e in emails if e.read_status == read_status]


def by_importance(emails: list[ProcessedEmail], level: ImportanceLevel) -> list[ProcessedEmail]:
    return [e for e in emails if e.importance_level == level]


def by_no_reply(emails: list[ProcessedEmail], is_no_reply: bool = True) -> list[ProcessedEmail]:
    return [e for e in emails if bool(e.is_no_reply) == is_no_reply]


def by_scheduling_related(emails: list[ProcessedEmail], is_scheduling_related: bool = True) -> list[ProcessedEmail]:
    return [e for e in emails if bool(e.is_scheduling_related) == is_scheduling_related]


def sorted_by_importance(emails: list[ProcessedEmail]) -> list[ProcessedEmail]:
    """Highest importance_score first; unscored emails sort last."""
    return sorted(
        emails,
        key=lambda e: e.importance_score if e.importance_score is not None else -1.0,
        reverse=True,
    )
