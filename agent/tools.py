"""Tools the in-app agent (agent/loop.py) can call.

Nine tools, each reusing an existing implementation rather than
reimplementing it:

  search_context        -> retrieval.pack.build_pack (real)
  get_email              -> pipeline.persist.get + ingestion.store.get (real)
  get_thread_brief       -> retrieval.briefs.get_brief (real)
  get_entity_brief       -> retrieval.briefs.get_brief, keyed by the entity's
                            own kind (real)
  list_entities           -> context.store.all_entities (real)
  find_open_items        -> node_brief.open_items (real)
  list_queue              -> api.filters.apply_filters/sort_emails (real)
  draft_reply             -> drafting.expand.expand_outline_to_full_draft (real)
  summarize_selection     -> pipeline.persist.get for each id + one LLM call

Every result returned by dispatch() is JSON-serializable and bounded (capped
list lengths, truncated text) — an unbounded get_email on a 5000-char body
blows the loop's context by turn three. dispatch() catches every exception a
tool raises and returns {"error": ...} instead of propagating it, so one bad
tool call doesn't abort the whole agent turn — same graceful-degradation
posture as pipeline/orchestrate.py's _run_stage.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from models.schema import EntityKind

MAX_BODY_CHARS = 4000  # same bound as drafting/expand.py's outline expansion
MAX_TEXT_CHARS = 1200
MAX_LIST_ITEMS = 25
MAX_QUEUE_ITEMS = 50
MAX_SUMMARIZE_SELECTION_IDS = 20


def _truncate(text: Optional[str], limit: int = MAX_TEXT_CHARS) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "…"


# --- tool specs (Anthropic tool-definition format) --------------------------

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "search_context",
        "description": (
            "Search across the whole mailbox's context graph — chunks, "
            "entities, and briefs, not just the currently open email. Use "
            "this to find facts that live in OTHER emails/threads than the "
            "one being discussed, e.g. a case or project mentioned earlier. "
            "Returns labeled excerpts, each citing the email(s) it came "
            "from."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for, in plain language."},
                "anchor_email_id": {
                    "type": "string",
                    "description": "Optional: the email currently being discussed, to prioritize its thread/case/project context.",
                },
                "k": {"type": "integer", "description": "Max results (default 8, capped at 25).", "minimum": 1},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_email",
        "description": (
            "Fetch one specific email by its id: subject, sender, summary, "
            "importance, category, reply outline status, and (truncated) "
            "original body. Use this when the user references a specific "
            "email or you already know its id from list_queue/search_context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email_id": {"type": "string"}},
            "required": ["email_id"],
        },
    },
    {
        "name": "get_thread_brief",
        "description": (
            "Get the standing state summary for one email thread: what's "
            "happened, who's involved, what's still open. Cheaper and more "
            "current than re-reading every message in the thread."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_entity_brief",
        "description": (
            "Get the standing state summary for one entity (a case, "
            "project, or person) — what's happened, what's still open, "
            "which emails are evidence. Use list_entities first if you "
            "don't already have the entity_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
    },
    {
        "name": "list_entities",
        "description": (
            "List known entities (people, orgs, cases, projects, "
            "deliverables, documents, topics) the mailbox has seen, "
            "optionally filtered by kind or a name search. Use this to "
            "find an entity_id before calling get_entity_brief, or to "
            "answer 'what cases/projects do we have going' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [k.value for k in EntityKind],
                    "description": "Optional: restrict to one entity kind.",
                },
                "query": {"type": "string", "description": "Optional: name/alias substring search."},
            },
        },
    },
    {
        "name": "find_open_items",
        "description": (
            "List open action items pulled from thread/case/project briefs, "
            "optionally filtered to one case (by entity_id) or one person "
            "(by email address). Use this to answer 'what's still "
            "outstanding' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string", "description": "Email address of the person to filter by."},
                "case": {"type": "string", "description": "entity_id of the case/project to filter by."},
            },
        },
    },
    {
        "name": "list_queue",
        "description": (
            "List processed emails from the review queue, filtered and "
            "sorted the same way the review UI does. Use this to answer "
            "'what's in my inbox', 'show me unread urgent mail', 'what "
            "needs a reply' — never guess at inbox contents, call this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "read_status": {"type": "string", "enum": ["read", "unread"]},
                "importance": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "category": {"type": "string", "description": "Free-form topic, e.g. 'billing'."},
                "no_reply": {"type": "boolean"},
                "scheduling": {"type": "boolean"},
                "has_outline": {"type": "boolean"},
                "search": {"type": "string", "description": "Substring match on subject/sender/summary."},
                "sort_by": {
                    "type": "string",
                    "enum": ["importance", "received", "sender", "category", "effort"],
                },
                "descending": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Max results (default 20, capped at 50).", "minimum": 1},
            },
        },
    },
    {
        "name": "draft_reply",
        "description": (
            "Expand an email's approved reply outline into a full draft the "
            "user can review. Requires the email to already have a reply "
            "outline (outlineEligible/replyOutline from get_email or "
            "list_queue) — this NEVER sends anything, it only produces text "
            "for the user to read and decide on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string"},
                "instructions": {
                    "type": "string",
                    "description": "Optional extra guidance to fold into the draft, e.g. 'mention we need this by Friday'.",
                },
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "summarize_selection",
        "description": (
            "Summarize a specific set of emails together (e.g. everything "
            "in one thread, or every email a user points at) into one "
            "combined summary, rather than reading each one individually."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The emails to summarize together (capped at 20).",
                }
            },
            "required": ["email_ids"],
        },
    },
]


# --- serialization of context-graph fixture/real objects ---------------------

def _entity_to_dict(entity: Any, aliases: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    # context.store.all_entities builds Entity from the `entity` row alone, so
    # `.aliases` is always empty there — aliases live in their own table. They
    # are passed in rather than read off the entity so the caller can fetch the
    # whole alias map in one query instead of one per entity.
    return {
        "entity_id": entity.entity_id,
        "kind": entity.kind.value if hasattr(entity.kind, "value") else entity.kind,
        "canonical_name": entity.canonical_name,
        "aliases": list(aliases or entity.aliases or []),
        "mention_count": entity.mention_count,
        "salience": entity.salience,
    }


def _brief_to_dict(brief: Any) -> Dict[str, Any]:
    return {
        "node_type": brief.node_type,
        "node_id": brief.node_id,
        "headline": brief.headline,
        "body": _truncate(brief.body_md),
        "open_items": brief.open_items[:MAX_LIST_ITEMS],
        "evidence_email_ids": brief.evidence_email_ids[:MAX_LIST_ITEMS],
    }


def _context_pack_to_dict(pack: Any, k: int) -> Dict[str, Any]:
    sections = pack.sections[: max(1, min(k, MAX_LIST_ITEMS))]
    return {
        "query": pack.query,
        "anchor_email_id": pack.anchor_email_id,
        "sections": [
            {
                "label": s.label,
                "text": _truncate(s.text),
                "source_email_ids": s.source_email_ids[:MAX_LIST_ITEMS],
                "score": s.score,
            }
            for s in sections
        ],
    }


# --- individual tools ---------------------------------------------------------

def _search_context(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from retrieval.pack import build_pack

    query = args["query"]
    k = int(args.get("k") or 8)
    pack = build_pack(
        anchor_email_id=args.get("anchor_email_id"), query=query, db_path=db_path
    )
    return _context_pack_to_dict(pack, k)


def _get_email(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from ingestion import store as raw_store
    from pipeline import persist

    email_id = args["email_id"]
    processed = persist.get(email_id, db_path)
    if processed is None:
        return {"error": "No processed email {0!r}".format(email_id)}

    from api.serializers import email_to_json

    payload = email_to_json(processed, include_calendar=False)
    raw = raw_store.get(email_id, db_path)
    payload["body"] = _truncate(raw.body if raw else None, MAX_BODY_CHARS)
    return payload


def _get_thread_brief(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from retrieval.briefs import get_brief

    thread_id = args["thread_id"]
    brief = get_brief("thread", thread_id, db_path=db_path)
    if brief is None:
        return {"error": "No brief for thread {0!r}".format(thread_id)}
    return _brief_to_dict(brief)


def _get_entity_brief(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    """Briefs are keyed (node_type, node_id), so the entity's own kind is
    what selects the row — reading it from the entity table first, rather
    than guessing "case" then "project", is what makes person/topic/
    deliverable briefs reachable too."""
    from retrieval.briefs import get_brief

    entity_id = args["entity_id"]
    entity = _entity_by_id(entity_id, db_path)
    if entity is None:
        return {"error": "No entity {0!r}".format(entity_id)}

    kind = entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
    brief = get_brief(kind, entity_id, db_path=db_path)
    if brief is None:
        return {
            "error": "No brief for entity {0!r} ({1})".format(
                entity.canonical_name, kind
            )
        }
    return _brief_to_dict(brief)


def _list_entities(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from context import store as context_store

    kind = EntityKind(args["kind"]) if args.get("kind") else None
    entities = context_store.all_entities(kind=kind, db_path=db_path)
    aliases = _alias_map(db_path)
    # all_entities has no text filter of its own; matching here keeps the
    # name/alias search the tool advertises without a second query shape.
    entities = _match_entities(entities, args.get("query"), aliases)

    # The list is capped, but the model needs to know it is looking at the
    # most salient N of a larger set — otherwise "list all projects" reads
    # as complete when it is the top 25 of 61.
    return {
        "entities": [
            _entity_to_dict(e, aliases.get(e.entity_id))
            for e in entities[:MAX_LIST_ITEMS]
        ],
        "total_matching": len(entities),
        "truncated": len(entities) > MAX_LIST_ITEMS,
    }


def _find_open_items(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    """Open items live in node_brief.open_items (a JSON array per brief), so
    this reads them straight out of the brief table rather than re-deriving
    them. A person/case argument narrows it to that entity's own brief."""
    from models import db as models_db

    target = args.get("person") or args.get("case")
    sql = "SELECT node_type, node_id, headline, open_items FROM node_brief"
    params: List[Any] = []

    if target:
        entity = _entity_by_name(target, db_path)
        if entity is None:
            return {"error": "No entity matching {0!r}".format(target)}
        sql += " WHERE node_id = ?"
        params.append(entity.entity_id)

    items: List[Dict[str, Any]] = []
    with models_db.connect(db_path) as conn:
        for row in conn.execute(sql, params):
            node_type, node_id, headline, open_items = row[0], row[1], row[2], row[3]
            for text in json.loads(open_items or "[]"):
                items.append(
                    {
                        "item": _truncate(text),
                        "node_type": node_type,
                        "node_id": node_id,
                        "headline": headline,
                    }
                )
                if len(items) >= MAX_LIST_ITEMS:
                    return {"items": items, "truncated": True}

    return {"items": items, "truncated": False}


# --- entity lookup shared by the brief/open-item tools ------------------------

def _entity_by_id(entity_id: str, db_path: Optional[Any]) -> Optional[Any]:
    from context import store as context_store

    for entity in context_store.all_entities(db_path=db_path):
        if entity.entity_id == entity_id:
            return entity
    return None


def _entity_by_name(name: str, db_path: Optional[Any]) -> Optional[Any]:
    """Most salient entity whose name or alias matches — the model passes a
    human name ("Priya", "Henderson"), not an entity_id."""
    from context import store as context_store

    matches = _match_entities(
        context_store.all_entities(db_path=db_path), name, _alias_map(db_path)
    )
    return matches[0] if matches else None


def _alias_map(db_path: Optional[Any]) -> Dict[str, List[str]]:
    """{entity_id: [alias, ...]} in one read."""
    from models import db as models_db

    out: Dict[str, List[str]] = {}
    with models_db.connect(db_path) as conn:
        for row in conn.execute("SELECT entity_id, alias FROM entity_alias"):
            out.setdefault(row[0], []).append(row[1])
    return out


def _match_entities(
    entities: Sequence[Any],
    query: Optional[str],
    aliases: Optional[Dict[str, List[str]]] = None,
) -> List[Any]:
    """Case-insensitive substring match over canonical name and aliases.
    all_entities already returns salience-ordered rows, so the filter
    preserves that ranking."""
    if not query:
        return list(entities)
    needle = query.strip().lower()
    if not needle:
        return list(entities)
    aliases = aliases or {}

    def matches(entity: Any) -> bool:
        if needle in (entity.canonical_name or "").lower():
            return True
        known = aliases.get(entity.entity_id) or entity.aliases or []
        return any(needle in (alias or "").lower() for alias in known)

    return [e for e in entities if matches(e)]


def _queue_item(email: Any) -> Dict[str, Any]:
    """A lean per-email projection for list_queue — the full email_to_json
    shape (summary, justification, calendar blob, proposed event, ...) is
    too much per row once you're listing up to 50 of them."""
    return {
        "email_id": email.email_id,
        "subject": email.subject or "",
        "sender": email.sender,
        "received_at": email.received_at.isoformat(),
        "read_status": email.read_status.value,
        "importance_level": email.importance_level.value if email.importance_level else None,
        "category": email.category,
        "is_no_reply": email.is_no_reply,
        "has_outline": bool(email.reply_outline),
    }


def _list_queue(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from api.filters import apply_filters, sort_emails
    from pipeline import persist

    emails = persist.all_processed(db_path)
    emails = apply_filters(
        emails,
        read_status=args.get("read_status"),
        importance=args.get("importance"),
        category=args.get("category"),
        no_reply=args.get("no_reply"),
        scheduling=args.get("scheduling"),
        has_outline=args.get("has_outline"),
        search=args.get("search"),
    )
    emails = sort_emails(
        emails,
        sort_by=args.get("sort_by") or "importance",
        descending=args.get("descending", True),
    )
    limit = max(1, min(int(args.get("limit") or 20), MAX_QUEUE_ITEMS))
    return {"total": len(emails), "emails": [_queue_item(e) for e in emails[:limit]]}


def _draft_reply(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from drafting.expand import NotYetImplementedError, expand_outline_to_full_draft
    from pipeline import persist

    email_id = args["email_id"]
    email = persist.get(email_id, db_path)
    if email is None:
        return {"error": "No processed email {0!r}".format(email_id)}
    if not email.reply_outline:
        return {"error": "Email {0!r} has no reply outline yet — it may be unread, no-reply, or not yet processed.".format(email_id)}

    outline = list(email.reply_outline)
    instructions = args.get("instructions")
    if instructions:
        # expand_outline_to_full_draft only accepts an outline, not free-text
        # instructions (see PHASES-COMPLEX.md plan's noted deviation) — fold
        # it in as one more bullet rather than modifying drafting/, which
        # this track doesn't own.
        outline.append("User instructions: {0}".format(instructions))

    try:
        draft = expand_outline_to_full_draft(email_id, outline=outline)
    except NotYetImplementedError as exc:
        return {"error": str(exc)}
    return {"email_id": email_id, "draft": draft}


_SUMMARIZE_SELECTION_SYSTEM_PROMPT = (
    "You summarize a set of emails the user has selected together, as one "
    "combined summary — not a per-email list. Cover what they're about "
    "collectively, any shared thread/topic, and anything that still needs "
    "action. Give your reason first, then the summary."
)

_SUMMARIZE_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 200},
        "summary": {"type": "string", "minLength": 50, "maxLength": 1200},
    },
    "required": ["reason", "summary"],
    "additionalProperties": False,
}


def _summarize_selection(args: Dict[str, Any], db_path: Optional[Any]) -> Dict[str, Any]:
    from llm.client import get_client, model_for
    from pipeline import persist

    email_ids = list(args["email_ids"])[:MAX_SUMMARIZE_SELECTION_IDS]
    found = []
    for email_id in email_ids:
        email = persist.get(email_id, db_path)
        if email is not None:
            found.append(email)
    if not found:
        return {"error": "None of the given email_ids were found."}

    lines = ["Emails to summarize together:"]
    for email in found:
        lines.append(
            "- {0!r} from {1}, subject {2!r}: {3}".format(
                email.email_id, email.sender, email.subject or "", email.summary or "(no summary yet)"
            )
        )
    user_message = "\n".join(lines)

    client = get_client("agent")
    response = client.messages.create(
        model=model_for("agent"),
        # Thinking tokens come out of this budget; 512 left almost nothing for
        # the summary itself once the model reasoned about the batch.
        max_tokens=1024,
        system=_SUMMARIZE_SELECTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_config={
            "format": {"type": "json_schema", "schema": _SUMMARIZE_SELECTION_SCHEMA},
            # Condensing already-written per-email summaries is mechanical, and
            # this runs as a nested call inside an agent turn -- the deep
            # reasoning that suits the outer loop is pure latency here.
            "effort": "low",
        },
    )
    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    return {
        "email_ids": [e.email_id for e in found],
        "summary": str(data["summary"]).strip(),
    }


_HANDLERS = {
    "search_context": _search_context,
    "get_email": _get_email,
    "get_thread_brief": _get_thread_brief,
    "get_entity_brief": _get_entity_brief,
    "list_entities": _list_entities,
    "find_open_items": _find_open_items,
    "list_queue": _list_queue,
    "draft_reply": _draft_reply,
    "summarize_selection": _summarize_selection,
}


def dispatch(name: str, args: Dict[str, Any], *, db_path: Optional[Any] = None) -> Dict[str, Any]:
    """Run one tool call. Always returns a JSON-serializable dict, never
    raises — a bad tool call becomes {"error": ...} for the model to see
    and adapt to, rather than aborting the agent turn."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": "Unknown tool {0!r}".format(name)}
    try:
        return handler(args, db_path)
    except KeyError as exc:
        return {"error": "Missing required argument: {0}".format(exc)}
    except Exception as exc:  # noqa: BLE001 - tool failures must not crash the loop
        return {"error": "{0}: {1}".format(type(exc).__name__, exc)}
