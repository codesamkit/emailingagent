"""Google Calendar OAuth: installed-app consent, token storage, and refresh.

Written scope-agnostic on purpose. `ingestion/gmail_auth.py` does the same
job for Gmail but hardcodes its own scope list, so the two cannot yet share
one implementation without editing that file. Keeping every Google-specific
value (scopes, token path, API name/version) a *parameter* here means this
module can later be hoisted verbatim into a shared `google_auth` helper and
ingestion's copy deleted, with no behavior change. See calendaring/README.md.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import config

log = logging.getLogger(__name__)


class MissingCredentialsError(RuntimeError):
    """Raised when the OAuth client secrets file is absent."""


def _load_token(token_file: Path, scopes: Sequence[str]) -> Optional[Credentials]:
    if not token_file.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(token_file), list(scopes))
    except (ValueError, KeyError) as exc:
        # A truncated or hand-edited token should not be a hard failure — drop
        # it and fall through to a fresh consent.
        log.warning("Ignoring unreadable token at %s (%s)", token_file, exc)
        return None


def _save_token(creds: Credentials, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    # The token grants calendar access — keep it owner-readable only.
    os.chmod(token_file, 0o600)


def _has_required_scopes(creds: Credentials, scopes: Sequence[str]) -> bool:
    """Whether a stored token already covers everything we need.

    A token minted before the write scope was added is still *valid*; it just
    can't do everything. Detecting that here turns a confusing 403 at call
    time into a re-consent at auth time.
    """
    granted = set(getattr(creds, "scopes", None) or [])
    return set(scopes).issubset(granted) if granted else False


def get_credentials(
    scopes: Optional[Sequence[str]] = None,
    credentials_file: Optional[Path] = None,
    token_file: Optional[Path] = None,
    allow_interactive: bool = True,
) -> Credentials:
    """Return usable credentials, refreshing or prompting for consent as needed.

    Order of preference: a valid stored token with sufficient scopes > a
    silent refresh > interactive browser consent. `allow_interactive=False`
    makes this safe to call from a non-TTY context, where it raises instead
    of hanging on a browser prompt.
    """
    scopes = list(scopes or config.SCOPES)
    credentials_file = Path(credentials_file or config.CREDENTIALS_FILE)
    token_file = Path(token_file or config.TOKEN_FILE)

    creds = _load_token(token_file, scopes)
    if creds and creds.valid and _has_required_scopes(creds, scopes):
        return creds

    if creds and not _has_required_scopes(creds, scopes):
        log.warning(
            "Stored token at %s lacks a required scope; re-running consent", token_file
        )
        creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_file)
            log.info("Refreshed expired Calendar token")
            return creds
        except Exception as exc:  # refresh token revoked/expired
            log.warning("Token refresh failed (%s); re-running consent", exc)

    if not allow_interactive:
        raise RuntimeError(
            "No valid Calendar token and interactive consent is disabled. "
            "Run: python -m calendaring.cli auth"
        )

    if not credentials_file.exists():
        raise MissingCredentialsError(
            "OAuth client secrets not found at {path}.\n"
            "Calendar reuses the same Desktop-app OAuth client as Gmail — see "
            "ingestion/README.md for how to create one, then make sure the "
            "Google Calendar API is enabled for that project (see "
            "calendaring/README.md). You can also point "
            "CALENDAR_CREDENTIALS_FILE somewhere else.".format(path=credentials_file)
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds, token_file)
    log.info("Stored new Calendar token at %s", token_file)
    return creds


def get_calendar_service(
    credentials_file: Optional[Path] = None,
    token_file: Optional[Path] = None,
    allow_interactive: bool = True,
    scopes: Optional[Sequence[str]] = None,
):
    """Build an authenticated Google Calendar API client."""
    creds = get_credentials(scopes, credentials_file, token_file, allow_interactive)
    # cache_discovery=False silences a noisy oauth2client warning on import and
    # avoids writing a discovery cache into the repo.
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_calendar_metadata(service, calendar_id: str = "primary") -> dict:
    """Id, display name, and timezone for a calendar.

    The timezone is the reason this exists: working hours are meaningless
    without knowing which clock they refer to.
    """
    from .retry import with_retry

    return with_retry(
        lambda: service.calendars().get(calendarId=calendar_id).execute(),
        description="calendars.get({0})".format(calendar_id),
    )
