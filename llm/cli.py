"""LLM backend CLI.

    python -m llm.cli routing                       # which stage goes where
    python -m llm.cli check                         # is Ollama up? model pulled?
    python -m llm.cli compare --stage summarize -n 5
    python -m llm.cli compare --stage score -n 10 --providers ollama anthropic
"""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap
import warnings
from typing import List, Optional, Sequence

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")

from ingestion import store

from . import config
from .client import describe
from .compare import STAGE_RUNNERS, compare, score_spread


def _truncate(text: str, width: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_routing(args: argparse.Namespace) -> int:
    print(describe())
    print("\nOverride per stage with env vars, e.g.:")
    print("  LLM_PROVIDER=ollama LLM_PROVIDER_OUTLINE=anthropic python -m pipeline.cli process")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Distinguish 'server down' from 'model not pulled' — different fixes."""
    from .ollama import OllamaClient, OllamaError

    client = OllamaClient()
    try:
        info = client.check()
    except OllamaError as exc:
        print("Ollama: UNREACHABLE\n  {0}".format(exc), file=sys.stderr)
        print("\nFix: install from https://ollama.com, then `ollama serve`.")
        print("For a remote host, start it with OLLAMA_HOST=0.0.0.0 so it is")
        print("not bound to loopback, and point OLLAMA_HOST here at that address.")
        return 1

    print("Ollama host  : {0}".format(info["host"]))
    print("Configured   : {0}".format(info["model"]))
    print("Pulled models: {0}".format(", ".join(info["available"]) or "(none)"))
    if not info["model_present"]:
        print("\nThe configured model is not pulled. Run:")
        print("  ollama pull {0}".format(info["model"]))
        return 1
    print("\nReady.")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    emails = store.recent(args.limit)
    if not emails:
        print("No raw emails stored. Run: python -m ingestion.cli ingest", file=sys.stderr)
        return 1

    print("Comparing stage {0!r} over {1} emails: {2}\n".format(
        args.stage, len(emails), " vs ".join(args.providers)))

    def progress(provider: str, done: int, total: int) -> None:
        if not args.quiet:
            print("\r  {0} {1}/{2}   ".format(provider, done, total), end="", file=sys.stderr)

    result = compare(args.stage, emails, providers=args.providers, on_progress=progress)
    if not args.quiet:
        print("", file=sys.stderr)

    for provider in result.providers():
        rows = result.results[provider]
        print("{0:<12} model={1:<22} failures={2}/{3}  mean={4:.1f}s".format(
            provider, rows[0].model if rows else "?", result.failures(provider),
            len(rows), result.mean_seconds(provider)))
        if args.stage == "score":
            spread = score_spread(rows)
            if spread.get("count"):
                print("             spread: min={min} max={max} mean={mean} "
                      "stdev={stdev} distinct={distinct}/{count}".format(**spread))
                if spread["distinct"] <= max(2, spread["count"] // 4):
                    print("             ^ CLUSTERED — plausible values, useless ranking")
    print()

    for index, email in enumerate(emails):
        print("-" * 78)
        print("{0}  |  {1}".format(_truncate(email.sender, 32), _truncate(email.subject, 40)))
        for provider in result.providers():
            row = result.results[provider][index]
            if not row.ok:
                print("  {0:<10} ERROR  {1}".format(provider, _truncate(row.error, 60)))
                continue
            value = row.value
            if args.stage == "score":
                rendered = "{0:>5.1f}  {1:<7} {2}".format(
                    value["score"], value["level"], _truncate(value["justification"], 44))
            elif args.stage == "classify":
                rendered = "{0!s:<6} {1}".format(value[0], _truncate(value[1], 54))
            elif args.stage == "summarize":
                summary, dates = value
                dates_note = "  [dates: {0}]".format(", ".join(dates)) if dates else ""
                rendered = _truncate(summary, 62 - len(dates_note)) + dates_note
            else:
                rendered = _truncate(value, 62)
            print("  {0:<10} {1}".format(provider, rendered))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m llm.cli",
        description="Inspect and compare LLM backends.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("routing", parents=[common], help="show which stage uses which backend")
    r.set_defaults(func=cmd_routing)

    c = sub.add_parser("check", parents=[common], help="check Ollama reachability and model")
    c.set_defaults(func=cmd_check)

    p = sub.add_parser("compare", parents=[common], help="run a stage under two backends")
    p.add_argument("--stage", required=True, choices=sorted(STAGE_RUNNERS))
    p.add_argument("-n", "--limit", type=int, default=5)
    p.add_argument("--providers", nargs="+", default=[config.OLLAMA, config.ANTHROPIC])
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_compare)

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


if __name__ == "__main__":
    sys.exit(main())
