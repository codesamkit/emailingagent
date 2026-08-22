"""FastAPI read/edit layer over `processed_email`.

Deliberately thin, and deliberately read-mostly. Processing is a batch job
(`python -m pipeline.cli process`) that takes minutes and makes many LLM
calls; serving is a fast read of rows that job already wrote. Keeping those
apart means no HTTP request ever waits on the pipeline, and the API needs no
long timeouts, no job queue, and no background workers to be correct.

The two mutating endpoints (edit an outline, expand a draft) are per-email
and human-triggered. Nothing here sends email or creates calendar events —
that stays out of the product until it is explicitly built and gated.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from models.schema import ReplyOutlineStatus
from pipeline import persist

from .auth import require_token
from .filters import SORT_FIELDS, apply_filters, sort_emails
from .serializers import email_to_json, emails_to_json

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
# deployed Valence origin once one exists — the Gmail add-on itself doesn't
# need an entry here, since Apps Script's UrlFetchApp isn't a browser and
# isn't subject to CORS.
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


@app.get("/api/emails/{email_id}", dependencies=[Depends(require_token)])
def get_email(email_id: str) -> Dict[str, Any]:
    """One email in full: processed fields, calendar context, and the
    original message text from `raw_email` (null if that row is gone,
    e.g. a database written before ingestion ran)."""
    email = persist.get(email_id, DB_PATH)
    if email is None:
        raise HTTPException(status_code=404, detail="No processed email {0!r}".format(email_id))
    payload = email_to_json(email, include_calendar=True)

    from ingestion import store as raw_store

    raw = raw_store.get(email_id, DB_PATH)
    payload["body"] = raw.body if raw else None
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
    }
