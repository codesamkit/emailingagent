"""Single source of truth for every calendar tunable.

Mirrors `ingestion/config.py`'s shape deliberately: every value is
environment-overridable so the CLI, the tests, and any future scheduled
runner share one configuration surface instead of each growing its own
defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent


def _path(env_var: str, default: Path) -> Path:
    raw = os.environ.get(env_var)
    return Path(raw).expanduser() if raw else default


def _int(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError("{0} must be an integer, got {1!r}".format(env_var, raw))


def _bool(env_var: str, default: bool) -> bool:
    raw = os.environ.get(env_var)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _hours(env_var: str, default: Tuple[int, int]) -> Tuple[int, int]:
    """Parse a working-hours window written as `9-17`."""
    raw = os.environ.get(env_var)
    if raw is None or not raw.strip():
        return default
    try:
        start, _, end = raw.strip().partition("-")
        parsed = (int(start), int(end))
    except ValueError:
        raise ValueError(
            "{0} must look like '9-17', got {1!r}".format(env_var, raw)
        )
    if not 0 <= parsed[0] < parsed[1] <= 24:
        raise ValueError(
            "{0} must satisfy 0 <= start < end <= 24, got {1!r}".format(env_var, raw)
        )
    return parsed


# --- OAuth -----------------------------------------------------------------
# Both scopes are requested at first consent, on purpose:
#   calendar.readonly -> freebusy.query + events.list (all this phase uses)
#   calendar.events   -> events insert/update, for a LATER phase
# Requesting the write scope now avoids a second consent prompt when event
# creation lands. Nothing in this package writes; see PHASES.md Phase 1B.
READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [READ_SCOPE, WRITE_SCOPE]


def _discover_credentials() -> Path:
    """Locate the OAuth client secrets file.

    Gmail and Calendar live in the same Google Cloud project and share one
    Desktop OAuth client, so the client-secrets path is reused from
    `ingestion.config` rather than re-derived. The fallback keeps this
    package importable on its own if ingestion is absent or renamed.
    """
    try:
        from ingestion.config import CREDENTIALS_FILE as shared
        return Path(shared)
    except Exception:
        conventional = REPO_ROOT / "credentials.json"
        if conventional.exists():
            return conventional
        downloaded = sorted(REPO_ROOT.glob("client_secret_*.json"))
        return downloaded[0] if downloaded else conventional


CREDENTIALS_FILE = _path("CALENDAR_CREDENTIALS_FILE", _discover_credentials())

# Deliberately NOT the repo-root `token.json` that ingestion uses: that token
# carries Gmail scopes only, and Google issues one token per scope set. Keeping
# them separate means re-consenting to Calendar never invalidates Gmail access.
TOKEN_FILE = _path("CALENDAR_TOKEN_FILE", PACKAGE_DIR / "data" / "calendar_token.json")

# --- Window ----------------------------------------------------------------
DEFAULT_WINDOW_DAYS = _int("CALENDAR_WINDOW_DAYS", 7)
CALENDAR_IDS = [
    cid.strip()
    for cid in os.environ.get("CALENDAR_IDS", "primary").split(",")
    if cid.strip()
]
# freebusy.query rejects requests asking about more than 50 calendars.
MAX_FREEBUSY_CALENDARS = 50
# events.list page size; the fetch paginates past this.
EVENTS_PAGE_SIZE = _int("CALENDAR_EVENTS_PAGE_SIZE", 250)

# --- Slot suggestion -------------------------------------------------------
DEFAULT_DURATION_MINUTES = _int("CALENDAR_DURATION_MINUTES", 30)
WORKING_HOURS = _hours("CALENDAR_WORKING_HOURS", (9, 17))
DEFAULT_MAX_SLOTS = _int("CALENDAR_MAX_SLOTS", 3)
INCLUDE_WEEKENDS = _bool("CALENDAR_INCLUDE_WEEKENDS", False)
# Proposed start times are snapped to this grid so suggestions read as
# "2:00pm" rather than "1:47pm".
SLOT_GRANULARITY_MINUTES = _int("CALENDAR_SLOT_GRANULARITY_MINUTES", 15)
# Never propose a slot that starts sooner than this — a reply suggesting a
# meeting six minutes from now is worse than no suggestion.
MIN_LEAD_MINUTES = _int("CALENDAR_MIN_LEAD_MINUTES", 60)
# Breathing room kept on either side of an existing commitment.
BUFFER_MINUTES = _int("CALENDAR_BUFFER_MINUTES", 0)

# IANA timezone for interpreting working hours. When unset, the primary
# calendar's own timezone is used (looked up once and cached), falling back
# to UTC if that lookup fails.
TIMEZONE = os.environ.get("CALENDAR_TIMEZONE") or None

# --- Rate-limit backoff ----------------------------------------------------
MAX_RETRIES = _int("CALENDAR_MAX_RETRIES", 5)
