"""Retry policy for Calendar API calls.

Thin adapter over `ingestion.backoff`, which is already a general Google API
retry (429/5xx/rate-limited-403, `Retry-After` honored, exponential backoff
with full jitter) despite living in the ingestion package. Reusing it means
there is one retry policy in the repo to reason about and tune, not two that
drift apart — the DRY rule in CLAUDE.md.

Only the retry *budget* is calendar-local, so it is passed explicitly rather
than inherited from ingestion's config.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from ingestion.backoff import is_retryable  # re-exported for callers/tests
from ingestion.backoff import with_retry as _with_retry

from . import config

T = TypeVar("T")

__all__ = ["with_retry", "is_retryable"]


def with_retry(
    call: Callable[[], T],
    description: str = "Calendar API call",
    max_retries: Optional[int] = None,
    sleep: Optional[Callable[[float], Any]] = None,
) -> T:
    """Run `call`, retrying transient failures with exponential backoff."""
    kwargs = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return _with_retry(
        call,
        description=description,
        max_retries=config.MAX_RETRIES if max_retries is None else max_retries,
        **kwargs,
    )
