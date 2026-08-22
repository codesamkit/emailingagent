"""Review interface (Phase 7): a CLI, not a web view - see PHASES.md's
Phase 7 proposal note for why. Shows emails sorted by importance, with
summary, a clear no-reply flag, an editable reply outline, and an
"expand to full draft" action. No send action anywhere - purely
human-in-the-loop review.

Data source: demo fixtures (interface/fixtures.py) by default, matching
Phase 5's fixture-development pattern; pass --db to read Phase 6's real
persisted output instead once that's wired in (pipeline.persist doesn't
touch the /calendar/ package, so this path works independently of the
calendar import-collision blocking pipeline/orchestrate.py's scheduling
branch - see that module's notes).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from drafting.expand import expand_outline_to_full_draft
from models.schema import ImportanceLevel, ProcessedEmail, ReadStatus, ReplyOutlineStatus
from pipeline.staleness import find_stale_outlines

from .fixtures import demo_processed_emails
from .filters import by_importance, by_no_reply, by_read_status, by_scheduling_related, sorted_by_importance

NO_REPLY_BADGE = "No-Reply — informational only"
STALE_OUTLINE_WARNING = "⚠ stale — a newer message has arrived in this thread"

_OUTLINE_PLACEHOLDER = {
    ReplyOutlineStatus.NONE: "Unread — no outline yet",
    ReplyOutlineStatus.NOT_APPLICABLE: "No-Reply — no response needed",
}

LIST_COLUMNS = ("importance", "read?", "sender", "subject", "no-reply?", "scheduling?", "has outline?", "stale?")


def load_emails(db_path: Optional[Path]) -> list[ProcessedEmail]:
    if db_path is None:
        return demo_processed_emails()

    from pipeline.persist import list_processed_emails

    return list_processed_emails(db_path=db_path)


def apply_filters(emails: list[ProcessedEmail], args: argparse.Namespace) -> list[ProcessedEmail]:
    if args.read_status:
        emails = by_read_status(emails, ReadStatus(args.read_status))
    if args.importance:
        emails = by_importance(emails, ImportanceLevel(args.importance))
    if args.no_reply:
        emails = by_no_reply(emails, True)
    if args.scheduling:
        emails = by_scheduling_related(emails, True)
    return emails


def _row(email: ProcessedEmail, stale_ids: frozenset[str]) -> tuple[str, ...]:
    return (
        email.importance_level.value if email.importance_level else "-",
        "yes" if email.read_status == ReadStatus.READ else "no",
        email.sender,
        email.subject,
        "yes" if email.is_no_reply else "no",
        "yes" if email.is_scheduling_related else "no",
        "yes" if email.reply_outline is not None else "no",
        "yes" if email.email_id in stale_ids else "no",
    )


def print_table(emails: list[ProcessedEmail], stale_ids: frozenset[str] = frozenset()) -> None:
    rows = [_row(e, stale_ids) for e in emails]
    widths = [
        max(len(LIST_COLUMNS[i]), *(len(r[i]) for r in rows)) if rows else len(LIST_COLUMNS[i])
        for i in range(len(LIST_COLUMNS))
    ]

    def fmt(row: tuple[str, ...]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(row, widths))

    print(fmt(LIST_COLUMNS))
    print("-+-".join("-" * w for w in widths))
    for row, email in zip(rows, emails):
        print(fmt(row))
        print(f"  id: {email.email_id}")


def format_outline_section(email: ProcessedEmail) -> str:
    if email.reply_outline is not None:
        return "\n".join(f"  - {bullet}" for bullet in email.reply_outline)
    return f"  ({_OUTLINE_PLACEHOLDER.get(email.reply_outline_status, 'No outline')})"


def print_detail(email: ProcessedEmail, stale_ids: frozenset[str] = frozenset()) -> None:
    print(f"From: {email.sender}")
    print(f"Subject: {email.subject}")
    if email.is_no_reply:
        print(NO_REPLY_BADGE)
    print(f"Importance: {email.importance_level.value if email.importance_level else '-'}")
    print(f"Summary: {email.summary or '(no summary)'}")
    if email.email_id in stale_ids:
        print(STALE_OUTLINE_WARNING)
    print(f"Reply outline [{email.reply_outline_status.value}]:")
    print(format_outline_section(email))


def find_email(emails: list[ProcessedEmail], email_id: str) -> ProcessedEmail:
    for email in emails:
        if email.email_id == email_id:
            return email
    raise KeyError(f"No email with id={email_id!r}")


def edit_outline(email: ProcessedEmail, bullets: list[str]) -> None:
    """Replace an existing outline with user-edited bullets. Refuses to
    create an outline for an email that was never eligible for one - the
    read/no-reply gate (drafting/outline.py) is what decides eligibility,
    not the interface."""
    if email.reply_outline is None:
        raise ValueError(
            f"email {email.email_id!r} has no outline to edit "
            f"(status={email.reply_outline_status.value}) - nothing to override"
        )
    email.reply_outline = bullets
    email.reply_outline_status = ReplyOutlineStatus.EDITED


def expand_to_full_draft(email_id: str) -> str:
    """Calls the Phase 5 stub. No auto-send - just returns text for the
    user to review, or a friendly message while it's unimplemented."""
    try:
        return expand_outline_to_full_draft(email_id)
    except NotImplementedError:
        return "(expand-to-full-draft isn't implemented yet - coming in a later phase)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review inbox emails: importance, summary, no-reply, reply outline.")
    parser.add_argument("--db", type=Path, default=None, help="Path to Phase 6's processed_email SQLite db (default: demo fixtures)")
    parser.add_argument("--read-status", choices=["read", "unread"], default=None, dest="read_status")
    parser.add_argument("--importance", choices=["low", "medium", "high", "urgent"], default=None)
    parser.add_argument("--no-reply", action="store_true", dest="no_reply")
    parser.add_argument("--scheduling", action="store_true")

    subparsers = parser.add_subparsers(dest="command")

    show = subparsers.add_parser("show", help="Show one email's full detail, including its outline")
    show.add_argument("email_id")

    edit = subparsers.add_parser("edit", help="Replace an existing outline's bullets")
    edit.add_argument("email_id")
    edit.add_argument("--bullet", action="append", required=True, dest="bullets", help="Repeatable; one per bullet")

    expand = subparsers.add_parser("expand", help="Expand an outline to a full draft (Phase 5 stub)")
    expand.add_argument("email_id")

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    all_emails = load_emails(args.db)
    # Staleness needs the full thread picture, so it's computed before
    # filtering narrows the list down - a filtered-out newer message
    # should still count toward marking an older one stale.
    stale_ids = frozenset(e.email_id for e in find_stale_outlines(all_emails))
    emails = apply_filters(all_emails, args)

    try:
        if args.command == "show":
            print_detail(find_email(emails, args.email_id), stale_ids)
        elif args.command == "edit":
            email = find_email(emails, args.email_id)
            edit_outline(email, args.bullets)
            print(f"Updated outline for {email.email_id} (status: {email.reply_outline_status.value})")
            print_detail(email, stale_ids)
        elif args.command == "expand":
            print(expand_to_full_draft(args.email_id))
        else:
            print_table(sorted_by_importance(emails), stale_ids)
    except (KeyError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
