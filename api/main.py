"""FastAPI read/edit layer over `processed_email`.

Deliberately thin, and deliberately read-mostly. Processing is a batch job
(`python -m pipeline.cli process`) that takes minutes and makes many LLM
calls; serving is a fast read of rows that job already wrote. Keeping those
apart means no HTTP request ever waits on the pipeline, and the API needs no
long timeouts, no job queue, and no background workers to be correct.

Every mutating endpoint (edit an outline, expand a draft, approve/decline a
proposed calendar event, update/cancel an approved one) is per-email and
human-triggered — none of them is ever called by the batch pipeline. There
is still no send-email endpoint; that stays out of the product until it is
explicitly built and gated the same way.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from calendaring import config as calendar_config
from models.schema import ProposedEventStatus, ReplyOutlineStatus
from pipeline import persist

from .auth import require_token
from .filters import SORT_FIELDS, apply_filters, sort_emails
from .serializers import calendar_to_json, email_to_json, emails_to_json, todo_to_json

log = logging.getLogger(__name__)

app = FastAPI(
    title="Email Agent API",
    version="0.1.0",
    description=(
        "Read layer over processed emails. Processing runs as a separate "
        "batch job; this API never triggers it."
    ),
)

# The frontend runs on a different port in dev. Origins are an explicit list
# rather than "*" so that turning this into a deployed app later is a config
# change, not a security review. EXTRA_ORIGINS (comma-separated) adds the
# deployed Valence origin once one exists — the Chrome extension itself
# doesn't need an entry here either: its background service worker fetches
# via host_permissions, not page-context CORS (see extension/background.js).
DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]
EXTRA_ORIGINS = [o.strip() for o in os.environ.get("EXTRA_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS + EXTRA_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# Overridden by tests to point at a temporary database.
DB_PATH: Optional[Path] = None

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """The review UI — a single self-contained page, so `uvicorn
    api.main:app` is the entire web MVP with no frontend build step."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", dependencies=[Depends(require_token)])
def health() -> Dict[str, Any]:
    """Liveness plus enough state to debug an empty-looking UI."""
    try:
        total = persist.count(DB_PATH)
    except Exception as exc:  # database missing or unreadable
        raise HTTPException(status_code=503, detail="database unavailable: {0}".format(exc))
    return {"status": "ok", "processedEmails": total}


@app.get("/api/emails", dependencies=[Depends(require_token)])
def list_emails(
    readStatus: Optional[str] = Query(None, pattern="^(read|unread)$"),
    importance: Optional[str] = Query(None, pattern="^(low|medium|high|urgent)$"),
    category: Optional[str] = Query(None, max_length=80),  # free-form topic, no pattern
    noReply: Optional[bool] = None,
    scheduling: Optional[bool] = None,
    hasOutline: Optional[bool] = None,
    search: Optional[str] = None,
    sortBy: str = Query("importance", pattern="^({0})$".format("|".join(SORT_FIELDS))),
    descending: bool = True,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """The review list: filtered, sorted, paginated.

    Filtering happens before pagination so `total` reflects the filtered set
    — paginating first would make the count meaningless.
    """
    emails = persist.all_processed(DB_PATH)
    emails = apply_filters(
        emails,
        read_status=readStatus,
        importance=importance,
        category=category,
        no_reply=noReply,
        scheduling=scheduling,
        has_outline=hasOutline,
        search=search,
    )
    emails = sort_emails(emails, sort_by=sortBy, descending=descending)
    total = len(emails)
    page = emails[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "emails": emails_to_json(page),
    }


def _related_context(email_id: str) -> Dict[str, Any]:
    """Case/project entities mentioned in this email, and other emails that
    share those entities — read directly from the context-graph tables
    (Checkpoint 0's chunk/entity/mention/relation, PHASES-COMPLEX.md).

    This is real wiring against real tables, not a fixture — it just has
    nothing to return until Track A's extraction pipeline (context/extract.py)
    actually populates `mention`, since nothing has written to that table yet.
    Kept here rather than in a context/store.py this track doesn't own.
    """
    from models import db as models_db

    with models_db.connect(DB_PATH) as conn:
        models_db.prepare(conn)
        entity_rows = conn.execute(
            "SELECT DISTINCT e.entity_id, e.kind, e.canonical_name "
            "FROM mention m JOIN entity e ON e.entity_id = m.entity_id "
            "WHERE m.email_id = ? AND e.kind IN ('case', 'project') "
            "ORDER BY e.salience DESC LIMIT 10",
            (email_id,),
        ).fetchall()
        entities = [dict(row) for row in entity_rows]

        related_email_ids: List[str] = []
        if entities:
            entity_ids = [e["entity_id"] for e in entities]
            placeholders = ",".join("?" for _ in entity_ids)
            rows = conn.execute(
                "SELECT DISTINCT email_id FROM mention "
                "WHERE entity_id IN ({0}) AND email_id != ? LIMIT 10".format(placeholders),
                (*entity_ids, email_id),
            ).fetchall()
            related_email_ids = [row["email_id"] for row in rows]

    return {
        "entities": [
            {"entityId": e["entity_id"], "kind": e["kind"], "name": e["canonical_name"]}
            for e in entities
        ],
        "relatedEmailIds": related_email_ids,
    }


@app.get("/api/emails/{email_id}", dependencies=[Depends(require_token)])
def get_email(email_id: str) -> Dict[str, Any]:
    """One email in full: processed fields, calendar context, the original
    message text from `raw_email` (null if that row is gone, e.g. a database
    written before ingestion ran), and its context-graph neighborhood."""
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    payload = email_to_json(email, include_calendar=True)

    from ingestion import store as raw_store

    raw = raw_store.get(email_id, DB_PATH)
    payload["body"] = raw.body if raw else None
    payload["relatedContext"] = _related_context(email_id)
    return payload


@app.patch("/api/emails/{email_id}/outline", dependencies=[Depends(require_token)])
def edit_outline(
    email_id: str,
    outline: List[str] = Body(..., embed=True),
) -> Dict[str, Any]:
    """Save a user-edited reply outline.

    Rejects edits to an email that is not outline-eligible. The gate is a
    correctness rule, not a UI affordance — a client that has drifted, or a
    direct API call, must not be able to attach a reply outline to an unread
    or no-reply email.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))

    from drafting.outline import is_eligible

    eligible, status = is_eligible(email)
    if not eligible:
        raise HTTPException(
            status_code=409,
            detail=(
                "This email is not eligible for a reply outline ({0}). "
                "Unread and no-reply emails never receive one.".format(status.value)
            ),
        )

    cleaned = [line.strip() for line in outline if line and line.strip()]
    persist.update_outline(email_id, cleaned or None, ReplyOutlineStatus.EDITED, DB_PATH)
    return email_to_json(persist.get(email_id, DB_PATH), include_calendar=True)


@app.delete("/api/emails/{email_id}/feedback", dependencies=[Depends(require_token)])
def clear_feedback(email_id: str, kind: Optional[str] = None) -> Dict[str, Any]:
    """Forget the corrections recorded for this email's sender.

    The counterpart to give_feedback. Because a prior overwrites the model's
    level in place, forgetting one cannot restore the old value — the sender's
    emails are re-scored instead, which is why this is scoped to a single
    sender rather than offering a global reset.

    `kind` optionally limits the clear to 'level' or 'no_reply'.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))

    from feedback import store as feedback_store
    from feedback.apply import apply_feedback
    from pipeline.refresh import apply_score_spread, rescore_sender

    try:
        removed = feedback_store.clear(email.sender, kind, db_path=DB_PATH)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    rescored = 0
    if removed:
        rescored = rescore_sender(email.sender, DB_PATH)
        # Any prior still standing for this sender (say, only 'level' was
        # cleared) must be re-applied on top of the fresh scores.
        changed = apply_feedback(persist.all_processed(DB_PATH), db_path=DB_PATH)
        if changed:
            persist.upsert(changed, DB_PATH)
        apply_score_spread(DB_PATH)

    return {
        "email": email_to_json(persist.get(email_id, DB_PATH), include_calendar=True),
        "removed": removed,
        "rescored": rescored,
    }


@app.post("/api/emails/{email_id}/feedback", dependencies=[Depends(require_token)])
def give_feedback(
    email_id: str,
    level: Optional[str] = Body(None),
    isNoReply: Optional[bool] = Body(None),
) -> Dict[str, Any]:
    """Record a correction and apply it immediately.

    One click teaches the whole inbox: the correction is stored as a prior
    on this email's *sender*, applied right now to every stored email from
    that sender, and re-applied after every future pipeline run — so the
    model's output can never silently undo it. Deterministic on purpose:
    a stored override fixes every future email from the sender with
    certainty, which few-shot prompt examples cannot promise.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    if level is None and isNoReply is None:
        raise HTTPException(status_code=422, detail="Provide level and/or isNoReply.")

    from feedback import store as feedback_store
    from feedback.apply import apply_feedback
    from pipeline.refresh import apply_score_spread

    try:
        if level is not None:
            feedback_store.record(email.sender, feedback_store.KIND_LEVEL,
                                  level, email_id=email_id, db_path=DB_PATH)
        if isNoReply is not None:
            feedback_store.record(email.sender, feedback_store.KIND_NO_REPLY,
                                  "true" if isNoReply else "false",
                                  email_id=email_id, db_path=DB_PATH)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    changed = apply_feedback(persist.all_processed(DB_PATH), db_path=DB_PATH)
    if changed:
        persist.upsert(changed, DB_PATH)
    apply_score_spread(DB_PATH)

    return {
        "email": email_to_json(persist.get(email_id, DB_PATH), include_calendar=True),
        "affected": len(changed),
    }


@app.post("/api/emails/{email_id}/expand", dependencies=[Depends(require_token)])
def expand_draft(email_id: str) -> Dict[str, Any]:
    """Expand an approved outline into full prose.

    Wired end-to-end now so the UI's button has a real contract; the
    underlying expansion is a later phase and currently returns 501 rather
    than placeholder prose. Even once implemented, this returns text for the
    user to review — it never sends anything.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    if not email.reply_outline:
        raise HTTPException(status_code=409, detail="This email has no outline to expand.")

    from drafting.expand import NotYetImplementedError, expand_outline_to_full_draft

    try:
        return {"emailId": email_id, "draft": expand_outline_to_full_draft(email_id, email.reply_outline)}
    except NotYetImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))


@app.post("/api/emails/{email_id}/refresh", dependencies=[Depends(require_token)])
def refresh_email(email_id: str) -> Dict[str, Any]:
    """Re-fetch one message from Gmail and process what it needs.

    The extension calls this when the user opens a message that is unread or
    unknown in the database — the read flip unlocks the outline within
    seconds instead of waiting for the next bulk refresh.
    """
    from googleapiclient.errors import HttpError

    from pipeline.refresh import refresh_one

    try:
        email = refresh_one(email_id, DB_PATH)
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        # Gmail answers 404 for a well-formed unknown id and 400 for a
        # malformed one — both mean "no such message" to our clients.
        if status in (400, 404):
            raise HTTPException(status_code=404, detail="Gmail has no message {0!r}".format(email_id))
        raise HTTPException(status_code=502, detail="Gmail error: {0}".format(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Gmail auth unavailable: {0}".format(exc))
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    # Same shape as GET /api/emails/{id} so clients can swap responses in.
    return get_email(email_id)


@app.post("/api/refresh", dependencies=[Depends(require_token)])
def refresh_pipeline(ingestLimit: Optional[int] = Query(None, ge=1, le=500)) -> Dict[str, Any]:
    """Ingest new Gmail messages and process whatever changed.

    Called by the Chrome extension so new mail and read-status flips get
    picked up without a manual pipeline run. Slow by nature (Gmail fetch +
    LLM stages); FastAPI runs sync endpoints in a threadpool, so it blocks
    only its own request. 409 means a refresh is already in flight — callers
    should wait and re-poll rather than retry.
    """
    from pipeline.refresh import RefreshBusyError, refresh

    try:
        result = refresh(DB_PATH, ingest_limit=ingestLimit)
    except RefreshBusyError:
        raise HTTPException(status_code=409, detail="A refresh is already running.")
    except RuntimeError as exc:
        # Covers MissingCredentialsError and the non-interactive token failure
        # from gmail_auth — the fix is a terminal action, not a retry.
        raise HTTPException(status_code=503, detail="Gmail auth unavailable: {0}".format(exc))
    # `plan` maps email_id -> stage tuple; useful in logs, noise on the wire.
    result.pop("plan", None)
    return result


_APPROVABLE_STATUSES = (ProposedEventStatus.SUGGESTED, ProposedEventStatus.FAILED)


@app.post("/api/emails/{email_id}/calendar-event/approve", dependencies=[Depends(require_token)])
def approve_calendar_event(email_id: str) -> Dict[str, Any]:
    """Create the proposed event on Google Calendar.

    The only HTTP-reachable path that writes to Calendar, and it only runs in
    direct response to this human click. The pipeline can also create events
    on its own when `calendaring.config.AUTO_ADD_EVENTS` is on, in which case
    an email arrives here already APPROVED and this route is the retry path
    for the ones auto-add deliberately skipped (past dates, multi-day spans).
    SUGGESTED (first attempt) and FAILED (the review UI's Retry button) are
    both acceptable starting states; APPROVED or DECLINED are not — the same
    correctness-over-convenience rule `edit_outline` applies to outlines: a
    stale or repeated approve must not double-book or silently no-op.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    if email.proposed_event is None or email.proposed_event_status not in _APPROVABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="This email has no pending proposed event to approve.",
        )

    from calendaring.events import create_event

    result = create_event(email.proposed_event)
    if result.google_event_id is None:
        persist.update_proposed_event_status(
            email_id, ProposedEventStatus.FAILED, result, DB_PATH
        )
        raise HTTPException(
            status_code=502,
            detail="Could not create the calendar event: {0}".format(result.error),
        )

    persist.update_proposed_event_status(email_id, ProposedEventStatus.APPROVED, result, DB_PATH)
    return email_to_json(persist.get(email_id, DB_PATH), include_calendar=True)


@app.post("/api/emails/{email_id}/calendar-event/decline", dependencies=[Depends(require_token)])
def decline_calendar_event(email_id: str) -> Dict[str, Any]:
    """Discard the proposed event. No Calendar API call is ever made.

    Also accepts FAILED (a user who saw a failed approve attempt and wants to
    give up on it instead of retrying) — same starting states as approve.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    if email.proposed_event is None or email.proposed_event_status not in _APPROVABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="This email has no pending proposed event to decline.",
        )

    persist.update_proposed_event_status(email_id, ProposedEventStatus.DECLINED, db_path=DB_PATH)
    return email_to_json(persist.get(email_id, DB_PATH), include_calendar=True)


def _calendar_http_error(exc: Any) -> HTTPException:
    """Map a googleapiclient HttpError for update/cancel, the way
    `refresh_email` maps Gmail's. 403 here almost always means the stored
    Calendar token predates the write scope — re-running
    `python -m calendaring.cli auth` re-consents, so the fix is "try again",
    not a code change.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 404:
        return HTTPException(status_code=404, detail="Calendar event not found — it may already be gone.")
    if status == 403:
        return HTTPException(
            status_code=503,
            detail=(
                "Calendar write access unavailable ({0}). Run `python -m "
                "calendaring.cli auth` to grant the write scope, then retry.".format(exc)
            ),
        )
    return HTTPException(status_code=502, detail="Calendar error: {0}".format(exc))


class UpdateProposedEventBody(BaseModel):
    summary: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[List[str]] = None


@app.post("/api/emails/{email_id}/calendar-event/update", dependencies=[Depends(require_token)])
def update_calendar_event(email_id: str, body: UpdateProposedEventBody) -> Dict[str, Any]:
    """Rename, reschedule, or otherwise edit an event already on Google
    Calendar. Only reachable once `approve` has succeeded (status APPROVED)
    — an event that was never created, or was declined, has nothing to edit.

    A failed edit leaves the stored event and its APPROVED status untouched:
    see calendaring/events.py's module docstring for why update_event raises
    instead of returning an error the way create_event does.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    proposed = email.proposed_event
    if (
        email.proposed_event_status != ProposedEventStatus.APPROVED
        or proposed is None
        or not proposed.google_event_id
    ):
        raise HTTPException(
            status_code=409,
            detail="This email has no approved calendar event to update.",
        )

    from googleapiclient.errors import HttpError

    from calendaring.events import update_event

    try:
        update_event(
            proposed.google_event_id,
            summary=body.summary,
            start=body.start,
            end=body.end,
            description=body.description,
            location=body.location,
            attendees=body.attendees,
        )
    except HttpError as exc:
        raise _calendar_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Google's API was told the same values; trust them locally rather than
    # re-parsing its response, the same way approve() trusts what it sent.
    updated = replace(
        proposed,
        title=body.summary if body.summary is not None else proposed.title,
        start=body.start if body.start is not None else proposed.start,
        end=body.end if body.end is not None else proposed.end,
        location=body.location if body.location is not None else proposed.location,
        description=body.description if body.description is not None else proposed.description,
        attendees=body.attendees if body.attendees is not None else proposed.attendees,
    )
    persist.update_proposed_event_status(email_id, ProposedEventStatus.APPROVED, updated, DB_PATH)
    return email_to_json(persist.get(email_id, DB_PATH), include_calendar=True)


@app.post("/api/emails/{email_id}/calendar-event/cancel", dependencies=[Depends(require_token)])
def cancel_calendar_event(email_id: str) -> Dict[str, Any]:
    """Delete an approved event from Google Calendar and mark the decision
    DECLINED — the same terminal state `decline` leaves a never-created
    proposal in, so the pipeline's terminal-status guard
    (pipeline/orchestrate.py) leaves this row alone on future runs either way.
    """
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    proposed = email.proposed_event
    if (
        email.proposed_event_status != ProposedEventStatus.APPROVED
        or proposed is None
        or not proposed.google_event_id
    ):
        raise HTTPException(
            status_code=409,
            detail="This email has no approved calendar event to cancel.",
        )

    from googleapiclient.errors import HttpError

    from calendaring.events import delete_event

    try:
        delete_event(proposed.google_event_id)
    except HttpError as exc:
        raise _calendar_http_error(exc)

    cleared = replace(proposed, google_event_id=None, error=None)
    persist.update_proposed_event_status(email_id, ProposedEventStatus.DECLINED, cleared, DB_PATH)
    return email_to_json(persist.get(email_id, DB_PATH), include_calendar=True)


@app.get("/api/calendar", dependencies=[Depends(require_token)])
def get_calendar(
    days: int = Query(
        calendar_config.DEFAULT_WINDOW_DAYS,
        ge=1,
        le=30,
        description="Days to look ahead. Bounded so one request can't fan out into a huge Calendar fetch.",
    )
) -> Dict[str, Any]:
    """The user's own calendar for the next `days` days, independent of any
    email.

    Read-only, and deliberately a thin wrapper: `get_calendar_context` already
    builds its own service and fetches busy blocks + expanded recurrences, and
    `calendar_to_json` already emits the shape the extension's per-email
    calendar section renders. Nothing here re-implements either.
    """
    from googleapiclient.errors import HttpError

    from calendaring.context import get_calendar_context

    try:
        context = get_calendar_context(days=days)
    except HttpError as exc:
        raise _calendar_http_error(exc)
    except Exception as exc:  # missing/expired token, no network, bad config
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not read your calendar ({0}). Run `python -m "
                "calendaring.cli auth` if this is the first run.".format(exc)
            ),
        )
    return calendar_to_json(context)


class AgentChatBody(BaseModel):
    message: str
    conversationId: Optional[str] = None


def _agent_event_to_json(event: Any, conversation_id: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"type": event.type}
    if event.type == "text_delta":
        payload["text"] = event.text
    elif event.type == "tool_start":
        payload["tool"] = event.tool
        payload["toolInput"] = event.tool_input
    elif event.type == "tool_end":
        payload["tool"] = event.tool
        payload["toolResult"] = event.tool_result
    elif event.type == "done":
        payload["conversationId"] = conversation_id
    return payload


@app.post("/api/agent/chat", dependencies=[Depends(require_token)])
def agent_chat(body: AgentChatBody) -> StreamingResponse:
    """Chat with the in-app agent over Server-Sent Events.

    Creates a conversation if none is given, appends the user's message,
    runs agent.loop.run, and streams its events out as they're produced —
    one JSON object per `data:` line. On the final "done" event, every
    message the loop generated (assistant turns and interleaved tool-result
    turns) is persisted via agent.conversation.append, so the next chat call
    against the same conversationId continues with full history.

    Any failure — a provider misconfiguration (agent routed to ollama,
    which has no tool-calling support), an upstream API error, a tool that
    raises — surfaces as a `{"type": "error", ...}` event inside the stream
    rather than an HTTP error status: by the time the failure is known, the
    200 response has already started. Letting one escape the generator
    instead truncates the SSE body mid-stream, which the client can only
    report as an unexplained network error.
    """
    from agent import conversation as agent_conversation
    from agent.loop import run as run_agent

    conversation_id = body.conversationId or agent_conversation.create(db_path=DB_PATH)
    agent_conversation.append(conversation_id, "user", body.message, DB_PATH)
    messages = agent_conversation.history(conversation_id, db_path=DB_PATH)

    def event_stream():
        try:
            for event in run_agent(messages, db_path=DB_PATH):
                if event.type == "done":
                    for new_message in event.new_messages:
                        agent_conversation.append(
                            conversation_id, new_message["role"], new_message["content"], DB_PATH
                        )
                yield "data: {0}\n\n".format(
                    json.dumps(_agent_event_to_json(event, conversation_id))
                )
        except Exception as exc:
            # Logged with a traceback for the server operator; the client
            # gets the message so a misconfiguration is self-explaining.
            log.exception("Agent chat failed (conversation %s)", conversation_id)
            message = str(exc) or exc.__class__.__name__
            yield "data: {0}\n\n".format(json.dumps({"type": "error", "error": message}))

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/agent/conversations/{conversation_id}", dependencies=[Depends(require_token)])
def get_agent_conversation(conversation_id: str) -> Dict[str, Any]:
    from agent import conversation as agent_conversation

    if agent_conversation.get(conversation_id, db_path=DB_PATH) is None:
        raise HTTPException(
            status_code=404, detail="No agent conversation {0!r}".format(conversation_id)
        )
    return {
        "conversationId": conversation_id,
        "messages": agent_conversation.history(conversation_id, db_path=DB_PATH),
    }


@app.get("/api/todos", dependencies=[Depends(require_token)])
def list_todos() -> Dict[str, Any]:
    """The derived to-do list: action items extracted from email bodies plus
    a "needs a reply" marker per unreplied email — see pipeline/todo.py.
    Open items only; completing one (below) removes it from this list."""
    from pipeline import todo

    items = todo.list_open(DB_PATH)
    return {"total": len(items), "todos": [todo_to_json(i) for i in items]}


@app.post("/api/todos/{todo_id}/complete", dependencies=[Depends(require_token)])
def complete_todo(todo_id: str) -> Dict[str, Any]:
    """Checks off one to-do item. Human-triggered only, like every other
    mutating endpoint in this file — the pipeline never marks one done."""
    from pipeline import todo

    if not todo.complete(todo_id, DB_PATH):
        raise HTTPException(
            status_code=404, detail="No open todo {0!r}".format(todo_id)
        )
    return {"ok": True}


@app.get("/api/stats", dependencies=[Depends(require_token)])
def stats() -> Dict[str, Any]:
    """Counts the review UI shows above the list."""
    emails = persist.all_processed(DB_PATH)
    return {
        "total": len(emails),
        "unread": sum(1 for e in emails if e.read_status.value == "unread"),
        "noReply": sum(1 for e in emails if e.is_no_reply),
        "scheduling": sum(1 for e in emails if e.is_scheduling_related),
        "withOutline": sum(1 for e in emails if e.reply_outline),
        "unprocessed": sum(1 for e in emails if e.processed_at is None),
        "byLevel": {
            level: sum(1 for e in emails if e.importance_level and e.importance_level.value == level)
            for level in ("urgent", "high", "medium", "low")
        },
        "byCategory": _top_categories(emails),
    }


def _top_categories(emails, limit: int = 12) -> Dict[str, int]:
    """Topic -> count for the filter chips, biggest first.

    Topics are free-form, so the UI shows only the most common ones — a
    long tail of one-off labels would be noise as filters, not signal.
    """
    counts: Dict[str, int] = {}
    for e in emails:
        if e.category:
            counts[e.category] = counts.get(e.category, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(ranked[:limit])
