"""Bearer-token gate for `/api/*`.

Local dev never needed this — only your own machine could reach
`localhost:8000`. Once the API is deployed somewhere network-reachable (for
the Gmail add-on to call it), that stops being true, so every `/api/*` route
depends on `require_token` instead of trusting the network boundary.

If `API_TOKEN` is unset, the gate is a no-op (`AUTH_DISABLED`) so local dev
and the existing test suite keep working without configuring a secret.
Setting `API_TOKEN` before deploying is what actually turns the gate on.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException


def _expected_token() -> Optional[str]:
    return os.environ.get("API_TOKEN") or None


def require_token(authorization: Optional[str] = Header(None)) -> None:
    expected = _expected_token()
    if expected is None:
        return  # AUTH_DISABLED: no token configured, e.g. local dev

    if authorization != "Bearer {0}".format(expected):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
