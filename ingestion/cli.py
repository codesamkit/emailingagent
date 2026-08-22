"""Manual ingestion CLI.

    python -m ingestion.cli auth                  # one-time OAuth consent
    python -m ingestion.cli ingest --limit 100    # fetch + store, print count + sample
    python -m ingestion.cli show --limit 10       # read back what's stored
    python -m ingestion.cli show --full <id>      # dump one full stored record
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings

# The Google client libraries emit end-of-life FutureWarnings on Python 3.9 and
# urllib3 warns about macOS's LibreSSL. Neither is actionable from here and both
# bury the CLI's actual output, so they are silenced at the entry point only.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")

import textwrap
from pathlib import Path
from typing import List, Optional, Sequence

from . import config, store
from .fetch import fetch_recent_emails
from .gmail_auth import MissingCredentialsError, get_gmail_service, get_profile
from .models import READ, RawEmail

# Sample-table layout: (heading, width, value function).
_COLUMNS = (
    ("SENDER", 30, lambda e: e.sender),
    ("SUBJECT", 42, lambda e: e.subject or "(no subject)"),
    ("RECEIVED (UTC)", 16, lambda e: e.received_at.strftime("%Y-%m-%d %H:%M")),
    ("READ?", 6, lambda e: "read" if e.read_status == READ else "UNREAD"),
    ("ATT?", 4, lambda e: "yes" if e.has_attachments else "-"),
    ("AUTOMATION HEADERS", 34, lambda e: ", ".join(e.hint_headers()) or "-"),
)


def _truncate(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_table(emails: Sequence[RawEmail]) -> None:
    header = "  ".join(head.ljust(width) for head, width, _ in _COLUMNS)
    print(header)
    print("  ".join("-" * width for _, width, _ in _COLUMNS))
    for email in emails:
        print(
            "  ".join(
                _truncate(value(email), width).ljust(width)
                for _, width, value in _COLUMNS
            )
        )


def _print_full(email: RawEmail) -> None:
    print("email_id       : {0}".format(email.email_id))
    print("thread_id      : {0}".format(email.thread_id))
    print("sender         : {0}".format(email.sender))
    print("subject        : {0}".format(email.subject))
    print("received_at    : {0}".format(email.received_at.isoformat()))
    print("read_status    : {0}".format(email.read_status.value))
    print("recipients     : {0}".format(", ".join(email.recipients) or "-"))
    print("label_ids      : {0}".format(", ".join(email.label_ids) or "-"))
    print("has_attachments: {0}".format(email.has_attachments))
    print("\nheaders:")
    for name, value in email.headers.items():
        print("  {0}: {1}".format(name, _truncate(value, 200)))
    print("\nbody ({0} chars):".format(len(email.body)))
    body = email.body or "(empty — attachment-only or bodyless message)"
    print(textwrap.indent(body[:2000], "  "))
    if len(body) > 2000:
        print("  ... [{0} more chars]".format(len(body) - 2000))


def _db_path(args: argparse.Namespace) -> Optional[Path]:
    return Path(args.db) if args.db else None


def cmd_auth(args: argparse.Namespace) -> int:
    """Run or refresh OAuth and confirm which account was authorized."""
    service = get_gmail_service()
    profile = get_profile(service)
    print("Authorized as : {0}".format(profile.get("emailAddress", "?")))
    print("Total messages: {0}".format(profile.get("messagesTotal", "?")))
    print("Token stored  : {0}".format(config.TOKEN_FILE))
    print("Scopes        : {0}".format(", ".join(config.SCOPES)))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Fetch the N most recent messages and upsert them into `raw_email`."""
    db_path = _db_path(args)
    service = get_gmail_service(allow_interactive=not args.non_interactive)

    def progress(done: int, total: int) -> None:
        if not args.quiet:
            print("\r  fetching {0}/{1}".format(done, total), end="", file=sys.stderr)

    emails: List[RawEmail] = list(
        fetch_recent_emails(
            service, limit=args.limit, query=args.query, on_progress=progress
        )
    )
    if not args.quiet:
        print("", file=sys.stderr)

    store.init_db(db_path)
    written = store.upsert_emails(emails, db_path)
    total = store.count(db_path)

    print("Query          : {0!r}".format(args.query))
    print("Fetched        : {0}".format(len(emails)))
    print("Stored/updated : {0}".format(written))
    print("Total in table : {0}".format(total))
    print("Database       : {0}".format(db_path or config.DB_PATH))

    sample = store.recent(args.sample, db_path)
    if sample:
        print("\nSample ({0} most recent stored):\n".format(len(sample)))
        _print_table(sample)
        print(
            "\nInspect one in full:  python -m ingestion.cli show --full {0}".format(
                sample[0].email_id
            )
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print stored rows without touching the network."""
    db_path = _db_path(args)
    if args.full:
        email = store.get(args.full, db_path)
        if email is None:
            print("No stored email with id {0!r}".format(args.full), file=sys.stderr)
            return 1
        _print_full(email)
        return 0

    total = store.count(db_path)
    emails = store.recent(args.limit, db_path)
    print("Total in table : {0}".format(total))
    print("Database       : {0}\n".format(db_path or config.DB_PATH))
    if not emails:
        print("Nothing stored yet. Run: python -m ingestion.cli ingest")
        return 0
    _print_table(emails)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ingestion.cli",
        description="Gmail ingestion (Track A, Phase 1) — read-only.",
    )
    # Shared flags live on a parent parser so they can be typed *after* the
    # subcommand (`... ingest --db x`), which is what people actually expect.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="store_true", help="show retry/backoff logging"
    )
    common.add_argument(
        "--db", default=None, help="SQLite path (default: {0})".format(config.DB_PATH)
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser(
        "auth", parents=[common], help="run or refresh Gmail OAuth consent"
    )
    p_auth.set_defaults(func=cmd_auth)

    p_ingest = sub.add_parser(
        "ingest", parents=[common], help="fetch recent emails and store them"
    )
    p_ingest.add_argument(
        "-n", "--limit", type=int, default=config.DEFAULT_LIMIT,
        help="how many messages to fetch (default: {0})".format(config.DEFAULT_LIMIT),
    )
    p_ingest.add_argument(
        "-q", "--query", default=config.DEFAULT_QUERY,
        help="Gmail search query (default: {0!r})".format(config.DEFAULT_QUERY),
    )
    p_ingest.add_argument(
        "--sample", type=int, default=5, help="rows to print after ingesting"
    )
    p_ingest.add_argument("--quiet", action="store_true", help="suppress progress")
    p_ingest.add_argument(
        "--non-interactive", action="store_true",
        help="fail instead of opening a browser if no valid token exists",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_show = sub.add_parser(
        "show", parents=[common], help="print stored emails (no network)"
    )
    p_show.add_argument("-n", "--limit", type=int, default=10)
    p_show.add_argument("--full", metavar="EMAIL_ID", help="dump one record in full")
    p_show.set_defaults(func=cmd_show)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except MissingCredentialsError as exc:
        print("\n{0}\n".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Normal when piping into `head`/`less` — the reader closed early.
        # Redirect stdout to devnull so the interpreter's shutdown flush
        # doesn't re-raise and print a spurious traceback.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
