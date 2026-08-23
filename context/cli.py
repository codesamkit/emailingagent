"""Context-graph CLI — the tool for judging whether the graph is any good.

    python -m context.cli build                   # run the context pass + consolidate
    python -m context.cli graph                   # THE go/no-go view
    python -m context.cli entities --kind case
    python -m context.cli email <email_id>
    python -m context.cli chunks <email_id>
    python -m context.cli check                   # is the embedding model reachable

Output is human-readable tables, not JSON. These are read with eyes.

`graph` is the gate. Before retrieval or an agent over this graph means
anything, `graph` has to show recognizable projects with their real cases and
people underneath. If cases come out fragmented — one node per mention instead
of one per case — the fix is in `context/resolve.py`'s threshold or
`context/normalize.py`, and it belongs here, before anything downstream
inherits a bad graph.

`build` lives in this CLI rather than in the pipeline because wiring the two
passes into `pipeline/refresh.py` is the integration step, and the graph has to
be inspectable before that. It runs the same `Pipeline.run_context` the
integrated path will, so it is not a second implementation.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from models.schema import ChunkKind, EntityKind, MentionSource

from . import consolidate as consolidate_module
from . import store

# Kinds printed in this order — the ones that carry the correlation first.
_KIND_ORDER = (
    EntityKind.PROJECT,
    EntityKind.CASE,
    EntityKind.PERSON,
    EntityKind.ORG,
    EntityKind.DELIVERABLE,
    EntityKind.DOCUMENT,
    EntityKind.TOPIC,
)


def _fit(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _table(headers: Sequence[str], widths: Sequence[int], rows) -> None:
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(_fit(str(cell), w).ljust(w) for cell, w in zip(row, widths)))


def _db(args) -> Optional[Path]:
    return Path(args.db) if args.db else None


# --- build ----------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    from ingestion import store as ingest_store
    from pipeline.incremental import context_plan
    from pipeline.orchestrate import CONTEXT_STAGES, Pipeline

    db_path = _db(args)
    raws = ingest_store.recent(args.limit or 10_000, db_path)
    if not raws:
        print("No raw emails stored. Run: python -m ingestion.cli ingest", file=sys.stderr)
        return 1

    coverage = {} if args.all else store.context_coverage(db_path=db_path)
    plan = context_plan(raws, coverage) if not args.all else {
        r.email_id: tuple(CONTEXT_STAGES) for r in raws
    }
    print("Context pass  : {0}/{1} emails need work".format(len(plan), len(raws)))

    if plan and not args.dry_run:
        todo = [r for r in raws if r.email_id in plan]
        by_stages: Dict[tuple, List] = {}
        for raw in todo:
            by_stages.setdefault(plan[raw.email_id], []).append(raw)

        errors: List[str] = []
        done, total = 0, len(todo)
        for stages, batch in by_stages.items():
            pipeline = Pipeline.with_defaults(stages=stages)
            # Persist per email rather than calling run_context on the batch.
            # The batch form returns a list, so nothing would reach disk until
            # all 163 emails had been extracted — no visible progress, and a
            # failure on the last one throws away every model call before it.
            for raw in batch:
                result = pipeline.run_context_one(raw)
                store.upsert_chunks(result.chunks, db_path=db_path)
                store.upsert_vectors(result.vectors, db_path=db_path)
                if result.mentions:
                    store.upsert_mentions(result.mentions, db_path=db_path)
                done += 1
                print("\r  {0} {1}/{2}  {3}".format(
                    ",".join(stages)[:22], done, total, _fit(raw.subject or "", 44)
                ).ljust(96), end="", file=sys.stderr)
            errors.extend(pipeline.errors)
        if total:
            print(file=sys.stderr)

        if errors:
            print("\nStage failures ({0}) — these retry on the next run:".format(len(errors)))
            for message in errors[:10]:
                print("  {0}".format(message))

    if args.dry_run:
        return 0

    print("\nConsolidating (corpus-wide) …")
    stats = consolidate_module.consolidate(db_path, threshold=args.threshold)
    for line in stats.as_lines():
        print("  {0}".format(line))
    print()
    _print_counts(db_path)
    return 0


def _print_counts(db_path: Optional[Path]) -> None:
    counts = store.counts(db_path=db_path)
    print("Tables        : " + ", ".join(
        "{0}={1}".format(name, value) for name, value in counts.items()
    ))


# --- graph ----------------------------------------------------------------

def cmd_graph(args: argparse.Namespace) -> int:
    db_path = _db(args)
    by_kind = store.entity_counts_by_kind(db_path=db_path)
    if not by_kind:
        print("Graph is empty. Run: python -m context.cli build", file=sys.stderr)
        return 1

    print("ENTITIES BY KIND")
    _table(
        ("KIND", "COUNT"),
        (14, 6),
        [
            (kind.value, by_kind.get(kind.value, 0))
            for kind in _KIND_ORDER
            if by_kind.get(kind.value)
        ],
    )
    print()
    _print_counts(db_path)

    email_counts = store.email_counts_for_entities(db_path=db_path)
    projects = store.all_entities(kind=EntityKind.PROJECT, db_path=db_path)
    if not projects:
        print("\nNo PROJECT entities. Either the corpus has none, or the extract "
              "stage is not returning any — check `context.cli email <id>`.")
    else:
        print("\nTOP PROJECTS, with the cases and people attached to each")
        print("(this is the view that says whether the whole approach works)\n")
        for project in projects[: args.limit]:
            _print_project(project, email_counts, db_path, args.depth)

    print("\nFRAGMENTATION CHECK — share of nodes appearing in only one email")
    rows = []
    for kind in _KIND_ORDER:
        entities = store.all_entities(kind=kind, db_path=db_path)
        if not entities:
            continue
        orphans = [
            e for e in entities if email_counts.get(e.entity_id, 0) <= 1
        ]
        rows.append((
            kind.value, len(entities), len(orphans),
            "{0:.0f}%".format(100.0 * len(orphans) / len(entities)),
        ))
    _table(("KIND", "NODES", "1-EMAIL", "SHARE"), (14, 6, 8, 6), rows)
    print("\n  Read this per kind. A high share on CASE or PROJECT means "
          "resolution is\n  splitting one real thing across several nodes — "
          "tune the threshold in\n  context/resolve.py or the normalization in "
          "context/normalize.py.\n  A high share on DOCUMENT or TOPIC is "
          "expected: an invoice or a serial\n  number genuinely appears once, "
          "and that is not fragmentation.")
    return 0


def _print_project(project, email_counts, db_path, depth: int) -> None:
    print("  ▸ {0}  [salience {1:.2f}, {2} emails, {3} mentions]".format(
        project.canonical_name, project.salience,
        email_counts.get(project.entity_id, 0), project.mention_count))
    neighbors = store.neighbors(project.entity_id, hops=depth, db_path=db_path)
    for kind, label in ((EntityKind.CASE, "cases"), (EntityKind.PERSON, "people")):
        rows = [
            (entity, weight, hop)
            for entity, weight, hop in neighbors
            if entity.kind == kind
        ]
        if not rows:
            continue
        print("      {0}:".format(label))
        for entity, weight, hop in rows[:12]:
            print("        - {0:<34} w={1:<7.2f} hop={2}  emails={3}".format(
                _fit(entity.canonical_name, 34), weight, hop,
                email_counts.get(entity.entity_id, 0)))
        if len(rows) > 12:
            print("        … and {0} more".format(len(rows) - 12))
    print()


# --- entities -------------------------------------------------------------

def cmd_entities(args: argparse.Namespace) -> int:
    db_path = _db(args)
    kind = EntityKind(args.kind) if args.kind else None
    entities = store.all_entities(kind=kind, db_path=db_path)
    if not entities:
        print("No entities{0}.".format(" of kind " + args.kind if args.kind else ""))
        return 0
    email_counts = store.email_counts_for_entities(db_path=db_path)
    print("{0} entit{1}{2}\n".format(
        len(entities), "y" if len(entities) == 1 else "ies",
        " of kind " + args.kind if args.kind else ""))
    _table(
        ("NAME", "KIND", "MENTIONS", "EMAILS", "SALIENCE", "ALIASES"),
        (36, 11, 8, 6, 8, 30),
        [
            (
                entity.canonical_name,
                entity.kind.value,
                entity.mention_count,
                email_counts.get(entity.entity_id, 0),
                "{0:.2f}".format(entity.salience),
                ", ".join(entity.aliases),
            )
            for entity in entities[: args.limit]
        ],
    )
    if len(entities) > args.limit:
        print("\n… and {0} more (--limit)".format(len(entities) - args.limit))
    return 0


# --- one email ------------------------------------------------------------

def cmd_email(args: argparse.Namespace) -> int:
    db_path = _db(args)
    from ingestion import store as ingest_store

    raw = ingest_store.get(args.email_id, db_path)
    if raw is None:
        print("No stored email with id {0!r}".format(args.email_id), file=sys.stderr)
        return 1

    print("email_id : {0}".format(raw.email_id))
    print("thread   : {0}".format(raw.thread_id))
    print("sender   : {0}".format(raw.sender))
    print("subject  : {0}".format(raw.subject))
    print("received : {0}".format(raw.received_at.isoformat()))

    mentions = store.mentions_for_email(args.email_id, db_path=db_path)
    if not mentions:
        print("\nNo mentions extracted. Run: python -m context.cli build")
        return 0

    entities = {
        entity.entity_id: entity
        for entity in store.entities_for_email(args.email_id, db_path=db_path)
    }
    # Grouped by which pass found it: a wrong regex is a different fix from the
    # model inventing something.
    for source in (MentionSource.HEADER, MentionSource.REGEX, MentionSource.LLM):
        group = [m for m in mentions if m.source == source]
        if not group:
            continue
        print("\n{0} ({1})".format(source.value.upper(), len(group)))
        _table(
            ("SPAN", "RESOLVED TO", "KIND", "CONF", "CHUNK"),
            (30, 30, 11, 5, 12),
            [
                (
                    mention.span_text,
                    (entities[mention.entity_id].canonical_name
                     if mention.entity_id in entities else mention.entity_id),
                    (entities[mention.entity_id].kind.value
                     if mention.entity_id in entities else "UNRESOLVED"),
                    "{0:.2f}".format(mention.confidence),
                    mention.chunk_id or "(subject)",
                )
                for mention in group
            ],
        )
    return 0


# --- chunks ---------------------------------------------------------------

def cmd_chunks(args: argparse.Namespace) -> int:
    db_path = _db(args)
    chunks = store.chunks_for_email(args.email_id, db_path=db_path)
    if not chunks:
        print("No chunks for {0!r}. Run: python -m context.cli build".format(
            args.email_id), file=sys.stderr)
        return 1

    tally: Dict[str, int] = {}
    for chunk in chunks:
        tally[chunk.kind.value] = tally.get(chunk.kind.value, 0) + 1
    print("{0} chunks: {1}\n".format(
        len(chunks), ", ".join("{0}={1}".format(k, v) for k, v in sorted(tally.items()))))
    print("Only kind=body is embedded and mined for entities. Read the body "
          "chunks below\nand confirm none of them contain quoted reply history "
          "or a signature block.\n")

    for chunk in chunks:
        marker = "██" if chunk.kind == ChunkKind.BODY else "░░"
        print("{0} [{1}] {2:<9} {3} chars".format(
            marker, chunk.ord, chunk.kind.value, len(chunk.text)))
        body = chunk.text if args.full else chunk.text[:400]
        print(textwrap.indent(body, "     "))
        if not args.full and len(chunk.text) > 400:
            print("     … ({0} more chars, --full to see)".format(len(chunk.text) - 400))
        print()
    return 0


# --- check ----------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    from llm.client import model_for
    from llm.embeddings import check as embed_check

    print("embeddings : {0}".format(embed_check()))
    print("extract    : {0}".format(model_for("extract")))
    _print_counts(_db(args))
    return 0


# --- parser ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m context.cli",
        description="Build and inspect the context graph.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--db", default=None, help="SQLite path")

    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", parents=[common], help="run the context pass + consolidate")
    b.add_argument("--all", action="store_true", help="redo every email, not just what's missing")
    b.add_argument("-n", "--limit", type=int, default=None)
    b.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    b.add_argument("--threshold", type=float, default=consolidate_module.DEFAULT_THRESHOLD,
                   help="entity-resolution cosine threshold (default %(default)s)")
    b.set_defaults(func=cmd_build)

    g = sub.add_parser("graph", parents=[common], help="the go/no-go view")
    g.add_argument("-n", "--limit", type=int, default=12, help="projects to show")
    g.add_argument("--depth", type=int, default=2, help="graph hops to walk")
    g.set_defaults(func=cmd_graph)

    e = sub.add_parser("entities", parents=[common], help="list entities")
    e.add_argument("--kind", choices=[k.value for k in EntityKind], default=None)
    e.add_argument("-n", "--limit", type=int, default=40)
    e.set_defaults(func=cmd_entities)

    m = sub.add_parser("email", parents=[common], help="what was extracted from one email")
    m.add_argument("email_id")
    m.set_defaults(func=cmd_email)

    c = sub.add_parser("chunks", parents=[common], help="every chunk of one email")
    c.add_argument("email_id")
    c.add_argument("--full", action="store_true", help="do not truncate chunk text")
    c.set_defaults(func=cmd_chunks)

    k = sub.add_parser("check", parents=[common], help="is the embedding model reachable")
    k.set_defaults(func=cmd_check)

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
