"""Pipeline CLI.

    python -m pipeline.cli process                  # incremental: only what changed
    python -m pipeline.cli process --all            # force full reprocess
    python -m pipeline.cli process --limit 10       # cap how many emails
    python -m pipeline.cli process --dry-run        # show the plan, run nothing
    python -m pipeline.cli process --skip outline   # disable a stage
    python -m pipeline.cli show                     # the review table
    python -m pipeline.cli show --full <email_id>   # one record in detail
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
import warnings
from pathlib import Path
from typing import List, Optional, Sequence

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")

from ingestion import store
from models.schema import ProcessedEmail, ReadStatus

from . import incremental, persist
from .orchestrate import STAGES, Pipeline

_COLUMNS = (
    ("SCORE", 5, lambda e: "-" if e.importance_score is None else "{0:.0f}".format(e.importance_score)),
    ("LEVEL", 7, lambda e: e.importance_level.value if e.importance_level else "-"),
    ("SENDER", 26, lambda e: e.sender),
    ("SUBJECT", 34, lambda e: e.subject or "(no subject)"),
    ("READ?", 6, lambda e: "read" if e.read_status == ReadStatus.READ else "UNREAD"),
    ("NO-REPLY", 8, lambda e: "yes" if e.is_no_reply else ("-" if e.is_no_reply is not None else "?")),
    ("SCHED", 5, lambda e: "yes" if e.is_scheduling_related else "-"),
    ("OUTLINE", 16, lambda e: e.reply_outline_status.value),
)


def _truncate(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_table(emails: Sequence[ProcessedEmail]) -> None:
    print("  ".join(head.ljust(w) for head, w, _ in _COLUMNS))
    print("  ".join("-" * w for _, w, _ in _COLUMNS))
    for email in emails:
        print("  ".join(_truncate(fn(email), w).ljust(w) for _, w, fn in _COLUMNS))


def _print_full(email: ProcessedEmail) -> None:
    print("email_id      : {0}".format(email.email_id))
    print("sender        : {0}".format(email.sender))
    print("subject       : {0}".format(email.subject))
    print("received_at   : {0}".format(email.received_at.isoformat()))
    print("read_status   : {0}".format(email.read_status.value))
    print("processed_at  : {0}".format(email.processed_at.isoformat() if email.processed_at else "-"))
    print("\nno_reply      : {0} ({1})".format(email.is_no_reply, email.no_reply_reason or "-"))
    print("importance    : {0} / {1}".format(
        "-" if email.importance_score is None else "{0:.1f}".format(email.importance_score),
        email.importance_level.value if email.importance_level else "-"))
    if email.importance_justification:
        print(textwrap.indent(textwrap.fill(email.importance_justification, 70), "  "))
    print("\nsummary:")
    print(textwrap.indent(textwrap.fill(email.summary or "(none)", 70), "  "))
    print("\nscheduling    : {0}".format(email.is_scheduling_related))
    if email.calendar_context:
        ctx = email.calendar_context
        print("  busy blocks : {0}".format(len(ctx.busy_blocks)))
        print("  suggested   : {0}".format(len(ctx.suggested_slots)))
    print("\nreply_outline ({0}):".format(email.reply_outline_status.value))
    for bullet in email.reply_outline or []:
        print(textwrap.indent(textwrap.fill("• " + bullet, 70), "  "))
    if not email.reply_outline:
        print("  (none)")


def _db(args) -> Optional[Path]:
    return Path(args.db) if args.db else None


def cmd_process(args: argparse.Namespace) -> int:
    db_path = _db(args)
    raws = store.recent(args.limit or 10_000, db_path)
    if not raws:
        print("No raw emails stored. Run: python -m ingestion.cli ingest", file=sys.stderr)
        return 1

    existing = {e.email_id: e for e in persist.all_processed(db_path)}

    if args.all:
        plan_map = {r.email_id: tuple(STAGES) for r in raws}
    else:
        plan_map = incremental.plan(raws, existing)

    if args.skip:
        plan_map = {
            eid: tuple(s for s in stages if s not in args.skip)
            for eid, stages in plan_map.items()
        }
        plan_map = {eid: s for eid, s in plan_map.items() if s}

    print(incremental.summarize_plan(plan_map, len(raws)))
    if args.dry_run:
        for email_id, stages in list(plan_map.items())[:20]:
            print("  {0}  {1}".format(email_id, ", ".join(stages)))
        return 0
    if not plan_map:
        return 0

    todo = [r for r in raws if r.email_id in plan_map]
    # One pipeline per distinct stage set keeps each email's disabled stages
    # genuinely disabled rather than filtered after the fact.
    written = 0
    by_stages: dict = {}
    for raw in todo:
        by_stages.setdefault(plan_map[raw.email_id], []).append(raw)

    all_errors: List[str] = []
    for stages, batch in by_stages.items():
        pipeline = Pipeline.with_defaults(stages=stages)

        def progress(done: int, total: int) -> None:
            if not args.quiet:
                print("\r  {0} {1}/{2}".format(",".join(stages)[:28], done, total),
                      end="", file=sys.stderr)

        results = pipeline.process(batch, existing=existing, on_progress=progress)
        if not args.quiet:
            print("", file=sys.stderr)
        written += persist.upsert(results, db_path)
        all_errors.extend(pipeline.errors)

    print("Processed     : {0}".format(written))
    print("Total stored  : {0}".format(persist.count(db_path)))
    if all_errors:
        print("\nStage failures ({0}) — these fields stay unset and retry next run:".format(len(all_errors)))
        for message in all_errors[:10]:
            print("  {0}".format(message))
        if len(all_errors) > 10:
            print("  ... and {0} more".format(len(all_errors) - 10))

    top = persist.all_processed(db_path, limit=args.sample)
    if top:
        print("\nTop {0} by importance:\n".format(len(top)))
        _print_table(top)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    db_path = _db(args)
    if args.full:
        email = persist.get(args.full, db_path)
        if email is None:
            print("No processed email with id {0!r}".format(args.full), file=sys.stderr)
            return 1
        _print_full(email)
        return 0

    emails = persist.all_processed(db_path, limit=args.limit)
    print("Total processed: {0}\n".format(persist.count(db_path)))
    if not emails:
        print("Nothing processed yet. Run: python -m pipeline.cli process")
        return 0
    _print_table(emails)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.cli",
        description="Run the processing pipeline and inspect its output.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--db", default=None, help="SQLite path")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", parents=[common], help="run the pipeline")
    p.add_argument("--all", action="store_true", help="reprocess everything, not just what changed")
    p.add_argument("-n", "--limit", type=int, default=None, help="cap how many emails")
    p.add_argument("--skip", nargs="+", choices=list(STAGES), default=[], help="disable stages")
    p.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    p.add_argument("--sample", type=int, default=10, help="rows to print afterwards")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_process)

    s = sub.add_parser("show", parents=[common], help="print processed emails")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--full", metavar="EMAIL_ID")
    s.set_defaults(func=cmd_show)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
