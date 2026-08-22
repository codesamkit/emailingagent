"""Google Calendar OAuth (read-only) — consent flow + token storage/refresh.

Scopes are read-only: freebusy queries and events.list only, nothing that
can create/modify events. Kept standalone from Gmail's OAuth (ingestion/
doesn't exist yet) — if ingestion/ later shares the same Google Cloud
project, its scopes can be merged into SCOPES here for a single combined
consent screen, but that's an ingestion-side decision to make later.
"""

from __future__ import annotations

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

_MODULE_DIR = os.path.dirname(__file__)
DEFAULT_CREDENTIALS_PATH = os.path.join(_MODULE_DIR, "credentials.json")
DEFAULT_TOKEN_PATH = os.path.join(_MODULE_DIR, "token_calendar.json")


def get_calendar_service(
    credentials_path: str = DEFAULT_CREDENTIALS_PATH,
    token_path: str = DEFAULT_TOKEN_PATH,
) -> Resource:
    """Return an authorized read-only Calendar API client.

    Runs the OAuth consent flow on first use (opens a local browser window)
    and stores the resulting token at `token_path` for reuse/refresh on
    subsequent calls.
    """
    creds: Credentials | None = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Missing OAuth client secrets at {credentials_path}. "
                    "In Google Cloud Console, create an OAuth client ID "
                    "(type: Desktop app), enable the Calendar API, download "
                    "the JSON, and save it at that path."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)
