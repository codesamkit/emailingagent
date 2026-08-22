"""Server-side filtering and sorting for the review list.

Lives on the server rather than in the frontend so the CLI, the web UI, and
any future client apply identical rules — and so a large inbox does not have
to be shipped to the browser just to be filtered.
"""

from __future__ import annotations

from typing import List, Optional

from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus

SORT_FIELDS = ("importance", "received", "sender", "category", "effort")


def apply_filters(
    emails: List[ProcessedEmail],
    read_status: Optional[str] = None,
    importance: Optional[str] = None,
    category: Optional[str] = None,
    no_reply: Optional[bool] = None,
    scheduling: Optional[bool] = None,
    has_outline: Optional[bool] = None,
    search: Optional[str] = None,
) -> List[ProcessedEmail]:
    """Filter the review list. Every argument is optional and ANDed together.

    `None` means "don't filter on this", which is deliberately distinct from
    `False` — `no_reply=False` means "only emails known NOT to be no-reply",
    and excludes unclassified ones whose flag is still None.
    """
    result = emails

    if read_status:
        wanted = ReadStatus(read_status)
        result = [e for e in result if e.read_status == wanted]

    if importance:
        wanted_level = ImportanceLevel(importance)
        result = [e for e in result if e.importance_level == wanted_level]

    if category:
        # Topics are free-form but normalized to lowercase at write time;
        # matching case-insensitively keeps hand-typed queries working too.
        wanted_topic = category.lower().strip()
        result = [e for e in result if (e.category or "").lower() == wanted_topic]

    if no_reply is not None:
        result = [e for e in result if e.is_no_reply is no_reply]

    if scheduling is not None:
        result = [e for e in result if bool(e.is_scheduling_related) is scheduling]

    if has_outline is not None:
        result = [e for e in result if bool(e.reply_outline) is has_outline]

    if search:
        needle = search.lower().strip()
        result = [
            e
            for e in result
            if needle in (e.subject or "").lower()
            or needle in (e.sender or "").lower()
            or needle in (e.summary or "").lower()
        ]

    return result


def sort_emails(
    emails: List[ProcessedEmail],
    sort_by: str = "importance",
    descending: bool = True,
) -> List[ProcessedEmail]:
    """Sort the review list. Unscored emails always sort last.

    Unscored last regardless of direction: an email with no score yet is not
    "the least important", it is "not yet known", and either end of the list
    is a worse place for it than the bottom.
    """
    if sort_by not in SORT_FIELDS:
        raise ValueError(
            "sort_by must be one of {0}, got {1!r}".format(SORT_FIELDS, sort_by)
        )

    if sort_by == "importance":
        scored = [e for e in emails if e.importance_score is not None]
        unscored = [e for e in emails if e.importance_score is None]
        scored.sort(key=lambda e: e.importance_score, reverse=descending)
        return scored + unscored

    if sort_by == "received":
        return sorted(emails, key=lambda e: e.received_at, reverse=descending)

    if sort_by == "category":
        # Uncategorized last for the same reason unscored sorts last; within a
        # topic, most important first so each group leads with what matters.
        labeled = [e for e in emails if e.category]
        unlabeled = [e for e in emails if not e.category]
        labeled.sort(key=lambda e: -(e.importance_score or 0))
        labeled.sort(key=lambda e: e.category.lower(), reverse=descending)
        return labeled + unlabeled

    if sort_by == "effort":
        from drafting.effort import effort_rank

        ranked = [(effort_rank(e), e) for e in emails]
        known = [(r, e) for r, e in ranked if r is not None]
        unknown = [e for r, e in ranked if r is None]
        # Ties broken by importance: among equally-quick replies, do the one
        # that matters most first.
        known.sort(key=lambda pair: -(pair[1].importance_score or 0))
        known.sort(key=lambda pair: pair[0], reverse=descending)
        return [e for _, e in known] + unknown

    return sorted(emails, key=lambda e: (e.sender or "").lower(), reverse=descending)
