"""Sort/filter helpers over scored ProcessedEmail records — matches the
frozen signatures in interfaces/README.md."""

from __future__ import annotations

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus


def top_n(emails: list[ProcessedEmail], n: int) -> list[ProcessedEmail]:
    """Top n emails by importance_score, descending. Emails with no score
    yet (None) sort last."""

    def sort_key(email: ProcessedEmail) -> float:
        return email.importance_score if email.importance_score is not None else -1.0

    return sorted(emails, key=sort_key, reverse=True)[:n]


def urgent_and_unread(emails: list[ProcessedEmail]) -> list[ProcessedEmail]:
    return [
        email
        for email in emails
        if email.importance_level == ImportanceLevel.URGENT and email.read_status == ReadStatus.UNREAD
    ]
