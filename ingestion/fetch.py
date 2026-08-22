"""Fetching recent Gmail messages, with pagination and rate-limit backoff."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

from googleapiclient.errors import HttpError

from . import config
from .backoff import with_retry
from .models import RawEmail
from .parse import to_raw_email

log = logging.getLogger(__name__)


def list_message_ids(
    service, limit: int, query: str = config.DEFAULT_QUERY
) -> List[str]:
    """Gmail message ids, newest first, paging until `limit` is reached.

    `users.messages.list` returns at most 500 ids per page, so anything above
    that (and any run where Gmail returns short pages) needs the page loop.
    """
    ids: List[str] = []
    page_token: Optional[str] = None

    while len(ids) < limit:
        remaining = limit - len(ids)
        request_args: Dict[str, Any] = {
            "userId": "me",
            "maxResults": min(remaining, config.MAX_PAGE_SIZE),
        }
        if query:
            request_args["q"] = query
        if page_token:
            request_args["pageToken"] = page_token

        response = with_retry(
            lambda: service.users().messages().list(**request_args).execute(),
            description="users.messages.list",
        )

        page = response.get("messages") or []
        ids.extend(str(m["id"]) for m in page)
        page_token = response.get("nextPageToken")
        log.debug("Listed %d ids (%d total), next_page=%s", len(page), len(ids), bool(page_token))

        if not page_token or not page:
            break

    return ids[:limit]


def get_message(service, message_id: str) -> Dict[str, Any]:
    """Fetch one full message resource."""
    return with_retry(
        lambda: service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute(),
        description="users.messages.get({0})".format(message_id),
    )


def fetch_recent_emails(
    service,
    limit: int = config.DEFAULT_LIMIT,
    query: str = config.DEFAULT_QUERY,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Iterator[RawEmail]:
    """Yield the `limit` most recent messages matching `query`, as `RawEmail`.

    Messages are fetched one at a time rather than through Gmail's batch
    endpoint: at these volumes (~5 quota units per message, against a 250
    units/second ceiling) batching buys nothing and costs a lot of complexity.

    A single message that 404s (deleted between the list and the get) or fails
    to parse is logged and skipped — one bad message must not abort the run.
    """
    message_ids = list_message_ids(service, limit=limit, query=query)
    total = len(message_ids)
    log.info("Fetching %d message(s) for query %r", total, query)

    for index, message_id in enumerate(message_ids, start=1):
        try:
            message = get_message(service, message_id)
            email = to_raw_email(message)
        except HttpError as exc:
            log.warning("Skipping message %s: %s", message_id, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad message shouldn't stop the run
            log.warning("Skipping unparseable message %s: %s", message_id, exc)
            continue
        if on_progress:
            on_progress(index, total)
        yield email
