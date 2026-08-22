"""CLI: `process_inbox` command (Phase 6, step 4).

Runs the full pipeline and prints one row per email:
sender | subject | importance | read? | no-reply? | scheduling? | has outline?
"""

from __future__ import annotations

import argparse

from models.schema import ProcessedEmail, ReadStatus

from .orchestrate import process_inbox

COLUMNS = ("sender", "subject", "importance", "read?", "no-reply?", "scheduling?", "has outline?")


def _row(processed: ProcessedEmail) -> tuple[str, ...]:
    return (
        processed.sender,
        processed.subject,
        processed.importance_level.value if processed.importance_level else "-",
        "yes" if processed.read_status == ReadStatus.READ else "no",
        "yes" if processed.is_no_reply else "no",
        "yes" if processed.is_scheduling_related else "no",
        "yes" if processed.reply_outline is not None else "no",
    )


def _print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [
        max(len(COLUMNS[i]), *(len(r[i]) for r in rows)) if rows else len(COLUMNS[i])
        for i in range(len(COLUMNS))
    ]

    def fmt(row: tuple[str, ...]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(row, widths))

    print(fmt(COLUMNS))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the full email pipeline and print a summary table.")
    parser.add_argument("--limit", type=int, default=50, help="Max emails to process")
    args = parser.parse_args(argv)

    processed_emails = process_inbox(limit=args.limit)
    _print_table([_row(p) for p in processed_emails])


if __name__ == "__main__":
    main()
