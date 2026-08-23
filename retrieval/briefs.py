"""Rollup briefs: cached, LLM-written state documents per thread/case/
project/person. PHASES-COMPLEX.md B4.

A brief is a STATE DOCUMENT (what's happened, who's involved, what's open,
what was decided), not a per-email digest — we already have per-email
summaries; concatenating them would be worthless.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

from models import db
from models.schema import Brief

from . import _graph_read

MIN_EVIDENCE_EMAILS = 2

SYSTEM_PROMPT = (
    "You maintain a rolling state document for one email case, project, or "
    "thread. You are given a list of evidence emails (sender, date, "
    "subject, and either their summary or an excerpt). Write a STATE "
    "DOCUMENT, not a list of per-email summaries: what has happened, who "
    "is involved, what has been decided, and what is still open. body_md "
    "is 2-5 short sentences or bullet points. open_items are concrete, "
    "unresolved actions — return an empty array if nothing is genuinely "
    "open, never a vague placeholder item."
)

# Field order is load-bearing (see scoring/score.py:92, classification/
# categorize.py:57) — reason first so it informs headline/body_md instead of
# rationalizing them after the fact. maxLength on every string.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 200},
        "headline": {"type": "string", "maxLength": 120},
        "body_md": {"type": "string", "maxLength": 2000},
        "open_items": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 8,
        },
    },
    "required": ["reason", "headline", "body_md", "open_items"],
    "additionalProperties": False,
}


def get_brief(node_type: str, node_id: str, db_path: Optional[Path] = None) -> Optional[Brief]:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM node_brief WHERE node_type = ? AND node_id = ?",
            (node_type, node_id),
        ).fetchone()
    return _row_to_brief(row) if row is not None else None


def _row_to_brief(row) -> Brief:
    return Brief(
        node_type=row["node_type"],
        node_id=row["node_id"],
        headline=row["headline"] or "",
        body_md=row["body_md"] or "",
        open_items=json.loads(row["open_items"]) if row["open_items"] else [],
        evidence_email_ids=(
            json.loads(row["evidence_email_ids"]) if row["evidence_email_ids"] else []
        ),
        evidence_hash=row["evidence_hash"],
        generated_at=(
            datetime.fromisoformat(row["generated_at"]) if row["generated_at"] else None
        ),
    )


def rebuild_dirty(
    db_path: Optional[Path] = None,
    *,
    limit: Optional[int] = None,
    client: Optional[Any] = None,
) -> int:
    """Rebuilds every case/project/thread brief whose evidence changed.

    Two cost gates, checked in this order: skip nodes with fewer than
    MIN_EVIDENCE_EMAILS emails (a single-email brief says nothing the
    summary didn't), then skip when evidence_hash is unchanged — that's the
    whole point of storing it, zero model calls on a no-op re-run. Returns
    the number of briefs actually (re)generated.
    """
    rebuilt = 0
    for node_type, node_id in _candidate_nodes(db_path):
        if limit is not None and rebuilt >= limit:
            break
        evidence_email_ids = _evidence_for(node_type, node_id, db_path)
        if len(evidence_email_ids) < MIN_EVIDENCE_EMAILS:
            continue
        current_hash = _evidence_hash(evidence_email_ids, db_path)
        existing = get_brief(node_type, node_id, db_path=db_path)
        if existing is not None and existing.evidence_hash == current_hash:
            continue
        brief = _generate(node_type, node_id, evidence_email_ids, db_path, client=client)
        _upsert_brief(brief, current_hash, db_path)
        rebuilt += 1
    return rebuilt


def _candidate_nodes(db_path: Optional[Path]) -> List[tuple]:
    """Every node that COULD have a brief: case/project entities, and every
    distinct thread. (Not person — no caller needs a person brief yet; see
    interfaces/README.md's context-graph section.)"""
    with db.connect(db_path) as conn:
        entity_rows = conn.execute(
            "SELECT entity_id, kind FROM entity WHERE kind IN ('case', 'project')"
        ).fetchall()
        thread_rows = conn.execute("SELECT DISTINCT thread_id FROM raw_email").fetchall()
    nodes = [(row["kind"], row["entity_id"]) for row in entity_rows]
    nodes += [("thread", row["thread_id"]) for row in thread_rows]
    return nodes


def _evidence_for(node_type: str, node_id: str, db_path: Optional[Path]) -> List[str]:
    if node_type == "thread":
        with db.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT email_id FROM raw_email WHERE thread_id = ?", (node_id,)
            ).fetchall()
        return [row["email_id"] for row in rows]
    # case / project — keyed by entity_id, evidence = every email that
    # mentions the entity (context.store.emails_for_entity's contract).
    return _graph_read.emails_for_entity(node_id, db_path=db_path)


def _evidence_hash(email_ids: Sequence[str], db_path: Optional[Path]) -> str:
    """Hash of the sorted email_ids plus each one's processed_at —
    PHASES-COMPLEX.md A5's documented evidence_hash contract, so a real
    dirty flag from context/consolidate.py (once that track lands) and this
    comparison never disagree. Falls back to raw_email.fetched_at when no
    processed_email row exists yet (e.g. in this fixture/these tests)."""
    from pipeline import persist

    parts = []
    for email_id in sorted(email_ids):
        processed = persist.get(email_id, db_path)
        if processed is not None and processed.processed_at is not None:
            stamp = processed.processed_at.isoformat()
        else:
            stamp = _fallback_stamp(email_id, db_path)
        parts.append("{0}:{1}".format(email_id, stamp))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _fallback_stamp(email_id: str, db_path: Optional[Path]) -> str:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT fetched_at FROM raw_email WHERE email_id = ?", (email_id,)
        ).fetchone()
    return row["fetched_at"] if row else ""


def _build_user_message(
    node_type: str, node_id: str, evidence_email_ids: Sequence[str], db_path: Optional[Path]
) -> str:
    from ingestion import store as raw_store
    from pipeline import persist

    lines = ["State document for {0} {1}. Evidence emails:".format(node_type, node_id)]
    for email_id in evidence_email_ids:
        raw = raw_store.get(email_id, db_path)
        if raw is None:
            continue
        processed = persist.get(email_id, db_path)
        summary = processed.summary if processed and processed.summary else None
        excerpt = summary or (raw.body or "")[:500]
        lines.append(
            "- From {0}, {1}, re: {2}: {3}".format(
                raw.sender, raw.received_at.date().isoformat(), raw.subject, excerpt
            )
        )
    return "\n".join(lines)


def _get_default_client() -> Any:
    from llm.client import get_client

    return get_client("brief")


def _default_model() -> str:
    from llm.client import model_for

    return model_for("brief")


def _generate(
    node_type: str,
    node_id: str,
    evidence_email_ids: Sequence[str],
    db_path: Optional[Path],
    client: Optional[Any] = None,
) -> Brief:
    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": _build_user_message(node_type, node_id, evidence_email_ids, db_path),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )
    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)

    return Brief(
        node_type=node_type,
        node_id=node_id,
        headline=str(data["headline"]),
        body_md=str(data["body_md"]),
        open_items=[str(item) for item in (data.get("open_items") or [])],
        evidence_email_ids=list(evidence_email_ids),
    )


def _upsert_brief(brief: Brief, evidence_hash: str, db_path: Optional[Path]) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO node_brief (node_type, node_id, headline, body_md, open_items, "
            "evidence_email_ids, evidence_hash, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(node_type, node_id) DO UPDATE SET "
            "headline = excluded.headline, body_md = excluded.body_md, "
            "open_items = excluded.open_items, "
            "evidence_email_ids = excluded.evidence_email_ids, "
            "evidence_hash = excluded.evidence_hash, "
            "generated_at = excluded.generated_at",
            (
                brief.node_type,
                brief.node_id,
                brief.headline,
                brief.body_md,
                json.dumps(brief.open_items),
                json.dumps(brief.evidence_email_ids),
                evidence_hash,
                now_iso,
            ),
        )
        conn.commit()
