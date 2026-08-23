"""build_pack — the ONLY context-assembly function in the codebase.
Outline generation, context-aware summarization, and (eventually) the agent
tool all call this; nobody builds context strings by hand.
PHASES-COMPLEX.md B3.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from models import db
from models.schema import ContextPack, ContextSection

from . import _graph_read
from .briefs import get_brief
from .search import search


def format_context_for_prompt(pack: Optional[ContextPack]) -> str:
    """Renders a ContextPack as a "What you already know:" block. Shared by
    drafting/outline.py and summarization/summarize.py so both
    context-aware prompts render a ContextPack identically — callers use
    build_pack's output directly, never re-assembling it by hand."""
    if not pack or not pack.sections:
        return ""
    lines = ["What you already know:"]
    for section in pack.sections:
        lines.append("[{0}] {1}".format(section.label, section.text))
    return "\n".join(lines)


def build_pack(
    *,
    anchor_email_id: Optional[str] = None,
    query: Optional[str] = None,
    budget_chars: int = 6000,
    db_path: Optional[Path] = None,
) -> ContextPack:
    """Fixed priority order until budget_chars is spent: anchor's own
    subject/summary -> thread brief (if the thread has >1 message) -> case/
    project briefs for the anchor's entities -> those briefs' open_items ->
    top-k foreign chunks from search(). Deduplicated by email_id; the
    anchor's own chunks never appear as foreign."""
    pack = ContextPack(query=query, anchor_email_id=anchor_email_id)
    remaining = budget_chars
    seen_email_ids = set()

    def _add(label: str, text: str, source_email_ids: List[str], score: float) -> None:
        nonlocal remaining
        if remaining <= 0 or not text:
            return
        text = text[:remaining]
        pack.sections.append(
            ContextSection(
                label=label, text=text, source_email_ids=source_email_ids, score=score
            )
        )
        remaining -= len(text)
        pack.total_chars += len(text)

    if anchor_email_id:
        seen_email_ids.add(anchor_email_id)
        _add_anchor_sections(_add, anchor_email_id, db_path)

    if remaining > 0 and (query or anchor_email_id):
        _add_foreign_sections(_add, seen_email_ids, query, anchor_email_id, db_path)

    return pack


def _add_anchor_sections(_add, anchor_email_id: str, db_path: Optional[Path]) -> None:
    from ingestion import store as raw_store
    from pipeline import persist

    anchor_raw = raw_store.get(anchor_email_id, db_path)
    anchor_processed = persist.get(anchor_email_id, db_path)

    # 1. the anchor email's own summary and subject
    if anchor_raw is not None:
        lines = ["Subject: {0}".format(anchor_raw.subject or "(no subject)")]
        if anchor_processed and anchor_processed.summary:
            lines.append("Summary: {0}".format(anchor_processed.summary))
        _add("This email", "\n".join(lines), [anchor_email_id], 1.0)

    # 2. the thread brief, if this thread has more than one message
    thread_id = anchor_raw.thread_id if anchor_raw is not None else None
    if thread_id:
        with db.connect(db_path) as conn:
            thread_count = conn.execute(
                "SELECT COUNT(*) FROM raw_email WHERE thread_id = ?", (thread_id,)
            ).fetchone()[0]
        if thread_count > 1:
            thread_brief = get_brief("thread", thread_id, db_path=db_path)
            if thread_brief is not None:
                _add(
                    "Thread so far",
                    "{0}\n{1}".format(thread_brief.headline, thread_brief.body_md),
                    thread_brief.evidence_email_ids,
                    0.9,
                )

    # 3 & 4. case/project briefs for the anchor's entities, then their open_items
    relevant_briefs = []
    for entity in _graph_read.entities_for_email(anchor_email_id, db_path=db_path):
        if entity["kind"] not in ("case", "project"):
            continue
        brief = get_brief(entity["kind"], entity["entity_id"], db_path=db_path)
        if brief is None:
            continue
        relevant_briefs.append(brief)
        _add(
            "{0}: {1}".format(entity["kind"].title(), entity["canonical_name"]),
            "{0}\n{1}".format(brief.headline, brief.body_md),
            brief.evidence_email_ids,
            0.8,
        )
    for brief in relevant_briefs:
        if brief.open_items:
            items_text = "\n".join("- {0}".format(item) for item in brief.open_items)
            _add(
                "Open items ({0})".format(brief.node_id),
                items_text,
                brief.evidence_email_ids,
                0.7,
            )


def _add_foreign_sections(
    _add,
    seen_email_ids: set,
    query: Optional[str],
    anchor_email_id: Optional[str],
    db_path: Optional[Path],
) -> None:
    # 5. top-k foreign chunks from search()
    for scored in search(query=query, anchor_email_id=anchor_email_id, db_path=db_path):
        if scored.email_id in seen_email_ids:
            continue
        seen_email_ids.add(scored.email_id)
        with db.connect(db_path) as conn:
            row = conn.execute(
                "SELECT sender, subject, received_at FROM raw_email WHERE email_id = ?",
                (scored.email_id,),
            ).fetchone()
        if row is None:
            continue
        label = "From {0}, {1}, re: {2}".format(
            row["sender"], (row["received_at"] or "")[:10], row["subject"] or "(no subject)"
        )
        _add(label, scored.text, [scored.email_id], scored.score)
