"""Retry-with-exponential-backoff for Gmail API calls.

Every network call in `fetch.py` goes through `with_retry`, so there is exactly
one retry policy to reason about and tune.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Callable, Optional, TypeVar

from googleapiclient.errors import HttpError

from . import config

log = logging.getLogger(__name__)

T = TypeVar("T")

# 5xx are transient server-side failures; 429 is the explicit rate-limit signal.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# 403 is overloaded: it means "rate limited" for these reasons and "you don't
# have permission" otherwise. Retrying a genuine permission error would just
# hide a misconfigured scope behind 30 seconds of silence, so only the
# rate-limit reasons are retried.
RETRYABLE_403_REASONS = frozenset(
    {"ratelimitexceeded", "userratelimitexceeded", "backenderror", "quotaexceeded"}
)

# Transport-level hiccups worth one more attempt.
RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError)


def _error_reason(error: HttpError) -> str:
    """Best-effort extraction of Google's machine-readable error reason."""
    try:
        payload = json.loads(error.content.decode("utf-8", errors="replace"))
        errors = payload.get("error", {}).get("errors") or []
        if errors:
            return str(errors[0].get("reason", "")).lower()
        return str(payload.get("error", {}).get("status", "")).lower()
    except Exception:
        return ""


def _status(error: HttpError) -> Optional[int]:
    resp = getattr(error, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_retryable(error: BaseException) -> bool:
    """Whether an exception represents a transient failure worth retrying."""
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True
    if not isinstance(error, HttpError):
        return False
    status = _status(error)
    if status in RETRYABLE_STATUSES:
        return True
    if status == 403:
        return _error_reason(error) in RETRYABLE_403_REASONS
    return False


def _retry_after(error: BaseException) -> Optional[float]:
    """Honor a server-supplied `Retry-After` header when one is present."""
    resp = getattr(error, "resp", None)
    if resp is None:
        return None
    try:
        value = resp.get("retry-after")
    except Exception:
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _delay_for(attempt: int) -> float:
    """Exponential backoff with full jitter, capped.

    Jitter matters here: without it, a batch of calls that all get rate-limited
    at once would retry in lockstep and get rate-limited again together.
    """
    window = min(
        config.BACKOFF_BASE_SECONDS * (config.BACKOFF_FACTOR ** attempt),
        config.BACKOFF_MAX_SECONDS,
    )
    return random.uniform(0.0, window)


def with_retry(
    call: Callable[[], T],
    description: str = "Gmail API call",
    max_retries: Optional[int] = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> T:
    """Run `call`, retrying transient failures with exponential backoff.

    Non-retryable errors propagate immediately — a bad scope or a deleted
    message should surface, not be buried under retries.
    """
    attempts = config.MAX_RETRIES if max_retries is None else max_retries
    last_error: BaseException

    for attempt in range(attempts + 1):
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - re-raised below when fatal
            if not is_retryable(error) or attempt == attempts:
                raise
            last_error = error
            delay = _retry_after(error)
            if delay is None:
                delay = _delay_for(attempt)
            log.warning(
                "%s failed (%s); retrying in %.1fs (attempt %d/%d)",
                description,
                type(error).__name__,
                delay,
                attempt + 1,
                attempts,
            )
            sleep(delay)

    raise last_error  # pragma: no cover - loop always returns or raises above
