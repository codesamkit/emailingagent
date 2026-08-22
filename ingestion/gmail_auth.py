"""Gmail OAuth: installed-app consent flow, token storage, and refresh.

Read-only scope only (`gmail.readonly`) — Phase 1 cannot send, modify, or
delete anything, by construction.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import config

log = logging.getLogger(__name__)


class MissingCredentialsError(RuntimeError):
    """Raised when the OAuth client secrets file is absent."""


def _load_token(token_file: Path) -> Optional[Credentials]:
    if not token_file.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(token_file), config.SCOPES)
    except (ValueError, KeyError) as exc:
        # A truncated or hand-edited token should not be a hard failure — drop
        # it and fall through to a fresh consent.
        log.warning("Ignoring unreadable token at %s (%s)", token_file, exc)
        return None


def _save_token(creds: Credentials, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    # The token grants inbox access — keep it owner-readable only.
    os.chmod(token_file, 0o600)


def get_credentials(
    credentials_file: Optional[Path] = None,
    token_file: Optional[Path] = None,
    allow_interactive: bool = True,
) -> Credentials:
    """Return usable credentials, refreshing or prompting for consent as needed.

    Order of preference: a valid stored token > a silent refresh > interactive
    browser consent. `allow_interactive=False` makes this safe to call from a
    non-TTY context, where it raises instead of hanging on a browser prompt.
    """
    credentials_file = credentials_file or config.CREDENTIALS_FILE
    token_file = token_file or config.TOKEN_FILE

    creds = _load_token(token_file)
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_file)
            log.info("Refreshed expired Gmail token")
            return creds
        except Exception as exc:  # refresh token revoked/expired
            log.warning("Token refresh failed (%s); re-running consent", exc)

    if not allow_interactive:
        raise RuntimeError(
            "No valid Gmail token and interactive consent is disabled. "
            "Run: python -m ingestion.cli auth"
        )

    if not Path(credentials_file).exists():
        raise MissingCredentialsError(
            "OAuth client secrets not found at {path}.\n"
            "Create a Desktop-app OAuth client in Google Cloud Console, download "
            "the JSON, and save it there (see ingestion/README.md). You can also "
            "point GMAIL_CREDENTIALS_FILE somewhere else.".format(path=credentials_file)
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_file), config.SCOPES
    )
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds, token_file)
    log.info("Stored new Gmail token at %s", token_file)
    return creds


def get_gmail_service(
    credentials_file: Optional[Path] = None,
    token_file: Optional[Path] = None,
    allow_interactive: bool = True,
):
    """Build an authenticated Gmail API client."""
    creds = get_credentials(credentials_file, token_file, allow_interactive)
    # cache_discovery=False silences a noisy oauth2client warning on import and
    # avoids writing a discovery cache into the repo.
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_profile(service) -> dict:
    """The authorized account's Gmail profile (address + message totals)."""
    from .backoff import with_retry

    return with_retry(
        lambda: service.users().getProfile(userId="me").execute(),
        description="users.getProfile",
    )
