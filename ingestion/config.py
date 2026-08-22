"""Single source of truth for every ingestion tunable.

All values are environment-overridable so the CLI, tests, and any future
scheduled runner share one configuration surface instead of each growing
their own defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root == parent of the `ingestion` package.
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
        raise ValueError(f"{env_var} must be an integer, got {raw!r}")


# --- OAuth -----------------------------------------------------------------
# Both scopes are requested at first consent, per the Phase 0 decision in
# CONTEXT.md: asking for the send scope now means the user is not prompted to
# re-consent when the send feature lands in a later phase.
#
# This does NOT make ingestion able to send. Nothing in this package calls
# users.messages.send; the write *code path* stays a future phase, gated
# behind an explicit user action in the review interface. Same pattern as
# calendaring/config.py's calendar.events scope.
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = [READ_SCOPE, SEND_SCOPE]

def _discover_credentials() -> Path:
    """Locate the OAuth client secrets file at the repo root.

    Google Cloud downloads the file as `client_secret_<client-id>.json`, so both
    that name and the conventional `credentials.json` are accepted — renaming a
    downloaded file is an easy step to forget and a confusing one to debug.
    `GMAIL_CREDENTIALS_FILE` always wins if set.
    """
    conventional = REPO_ROOT / "credentials.json"
    if conventional.exists():
        return conventional
    downloaded = sorted(REPO_ROOT.glob("client_secret_*.json"))
    return downloaded[0] if downloaded else conventional


CREDENTIALS_FILE = _path("GMAIL_CREDENTIALS_FILE", _discover_credentials())
TOKEN_FILE = _path("GMAIL_TOKEN_FILE", REPO_ROOT / "token.json")

# --- Storage ---------------------------------------------------------------
DB_PATH = _path("EMAIL_AGENT_DB", PACKAGE_DIR / "data" / "emails.db")

# --- Fetch -----------------------------------------------------------------
DEFAULT_LIMIT = _int("INGEST_LIMIT", 100)
DEFAULT_QUERY = os.environ.get("INGEST_QUERY", "label:inbox")

# Gmail caps users.messages.list at 500 ids per page.
MAX_PAGE_SIZE = 500

# --- Rate-limit backoff ----------------------------------------------------
MAX_RETRIES = _int("INGEST_MAX_RETRIES", 5)
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_MAX_SECONDS = 32.0
