"""Retrieval CLI.

    python -m retrieval.cli search "<query>"           # ranked hits, by channel
    python -m retrieval.cli pack --email <email_id>     # the ContextPack an outline/summary would get
    python -m retrieval.cli brief case <entity_id>      # a stored rollup brief
    python -m retrieval.cli brief thread <thread_id>
    python -m retrieval.cli rebuild                     # (re)generate stale/missing briefs

`pack --email` is the one to reach for when an outline comes out generic —
it shows whether the context was empty, or present and the model ignored it.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import warnings
from pathlib import Path
from typing import Optional, Sequence

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")

from . import briefs
from .pack import build_pack
from .search import search as run_search


def _db(args: argparse.Namespace) -> Optional[Path]:
    return Path(args.db) if args.db else None


def _truncate(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_search(args: argparse.Namespace) -> int:
    db_path = _db(args)
    results = run_search(args.query, k=args.limit, db_path=db_path)
    if not results:
        print("No hits.")
        return 0
    print("{0:<8} {1:<10} {2:<24} {3}".format("SCORE", "CHANNEL", "EMAIL", "TEXT"))
    print("-" * 90)
    for r in results:
        print(
            "{0:<8.4f} {1:<10} {2:<24} {3}".format(
                r.score, r.channel, r.email_id, _truncate(r.text, 60)
            )
        )
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    db_path = _db(args)
    if not args.email and not args.query:
        print("Provide --email, --query, or both.", file=sys.stderr)
        return 1

    pack = build_pack(
        anchor_email_id=args.email,
        query=args.query,
        budget_chars=args.budget,
        db_path=db_path,
    )
    print("query          : {0}".format(pack.query or "-"))
    print("anchor_email_id: {0}".format(pack.anchor_email_id or "-"))
    print("total_chars    : {0}\n".format(pack.total_chars))
    if not pack.sections:
        print("(empty pack)")
        return 0
    for i, section in enumerate(pack.sections, start=1):
        print(
            "[{0}] {1}  ({2} chars, score={3:.2f})".format(
                i, section.label, len(section.text), section.score
            )
        )
        print(textwrap.indent(textwrap.fill(section.text, 78), "    "))
        print("    sources: {0}".format(", ".join(section.source_email_ids) or "-"))
        print()
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    db_path = _db(args)
    brief = briefs.get_brief(args.node_type, args.node_id, db_path=db_path)
    if brief is None:
        print("No stored brief for {0} {1!r}.".format(args.node_type, args.node_id))
        return 1

    print("node_type    : {0}".format(brief.node_type))
    print("node_id      : {0}".format(brief.node_id))
    print("headline     : {0}".format(brief.headline))
    print(
        "generated_at : {0}".format(
            brief.generated_at.isoformat() if brief.generated_at else "-"
        )
    )
    print("\nbody:")
    print(textwrap.indent(textwrap.fill(brief.body_md, 78), "  "))
    print("\nopen_items:")
    if brief.open_items:
        for item in brief.open_items:
            print("  - {0}".format(item))
    else:
        print("  (none)")
    print("\nevidence_email_ids: {0}".format(", ".join(brief.evidence_email_ids) or "-"))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """Generate the briefs that are missing or whose evidence moved.

    rebuild_dirty already owns the decision of what needs regenerating -- this
    only reports it. Every brief is one model call, so --limit exists to price
    a run before committing to the whole corpus, and the stage is worth
    routing explicitly (LLM_PROVIDER_BRIEF=anthropic) since a rollup over a
    dozen emails is exactly where a small local model produces mush.
    """
    db_path = _db(args)
    from llm.client import model_for
    from llm.config import provider_for

    print(
        "Rebuilding briefs via {0}/{1} (limit={2})...".format(
            provider_for("brief"), model_for("brief"), args.limit or "none"
        )
    )
    count = briefs.rebuild_dirty(db_path, limit=args.limit)
    print("Generated {0} brief(s).".format(count))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m retrieval.cli",
        description="Inspect hybrid search, context packs, and rollup briefs.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None, help="SQLite path")

    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", parents=[common], help="ranked hybrid search results")
    s.add_argument("query", help="free-text query")
    s.add_argument("-n", "--limit", type=int, default=12)
    s.set_defaults(func=cmd_search)

    p = sub.add_parser(
        "pack", parents=[common], help="the ContextPack an outline/summary would receive"
    )
    p.add_argument("--email", metavar="EMAIL_ID", default=None, help="anchor email id")
    p.add_argument("--query", default=None, help="optional query alongside/instead of an anchor")
    p.add_argument("--budget", type=int, default=6000, help="budget_chars")
    p.set_defaults(func=cmd_pack)

    r = sub.add_parser(
        "rebuild", parents=[common], help="(re)generate missing or stale rollup briefs"
    )
    r.add_argument(
        "--limit", type=int, default=None, help="stop after this many briefs (cost control)"
    )
    r.set_defaults(func=cmd_rebuild)

    b = sub.add_parser("brief", parents=[common], help="print a stored rollup brief")
    b.add_argument("node_type", choices=["thread", "case", "project", "person"])
    b.add_argument("node_id")
    b.set_defaults(func=cmd_brief)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
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
