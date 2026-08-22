"""Hand-built Gmail API message resources for offline tests.

Shaped exactly like `users.messages.get(format="full")` responses so the parse
layer is exercised against realistic input without any network access.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional


def b64(text: str) -> str:
    """Encode as Gmail does: base64url, unpadded."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def headers(**kwargs: str) -> List[Dict[str, str]]:
    return [{"name": name.replace("_", "-"), "value": value} for name, value in kwargs.items()]


def message(
    msg_id: str = "m1",
    thread_id: str = "t1",
    label_ids: Optional[List[str]] = None,
    internal_date: str = "1755864000000",  # 2025-08-22T12:00:00Z
    snippet: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"] if label_ids is None else label_ids,
        "internalDate": internal_date,
        "snippet": snippet,
        "payload": payload or {},
    }


# --- payloads --------------------------------------------------------------

PLAIN_ONLY = {
    "mimeType": "text/plain",
    "headers": headers(
        From="Dana Reed <dana@example.com>",
        To="me@example.com",
        Subject="Lunch Thursday?",
        Date="Fri, 22 Aug 2025 12:00:00 +0000",
    ),
    "body": {"data": b64("Are you free Thursday at 1pm?\n\nDana")},
}

HTML_ONLY = {
    "mimeType": "text/html",
    "headers": headers(
        From="Acme News <news@acme.example>",
        To="me@example.com",
        Subject="Your weekly digest",
        List_Unsubscribe="<https://acme.example/u/1>, <mailto:u@acme.example>",
        Precedence="bulk",
    ),
    "body": {
        "data": b64(
            "<html><head><style>p{color:red}</style><title>Digest</title></head>"
            "<body><p>Hi&nbsp;there</p><script>alert('x')</script>"
            "<div>Read our <a href='#'>latest post</a> &amp; enjoy.</div></body></html>"
        )
    },
}

# multipart/alternative: a plain part and an HTML part. The plain part wins.
MULTIPART_ALTERNATIVE = {
    "mimeType": "multipart/alternative",
    "headers": headers(
        From="Sam Okafor <sam@example.com>",
        Subject="Re: Q3 numbers",
        Auto_Submitted="auto-generated",
    ),
    "parts": [
        {"mimeType": "text/plain", "body": {"data": b64("The plain version.")}},
        {
            "mimeType": "text/html",
            "body": {"data": b64("<p>The <b>HTML</b> version.</p>")},
        },
    ],
}

# multipart/mixed wrapping a nested alternative, plus a real attachment.
NESTED_WITH_ATTACHMENT = {
    "mimeType": "multipart/mixed",
    "headers": headers(From="Ops <ops@example.com>", Subject="Report attached"),
    "parts": [
        {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("See attached.")}},
                {"mimeType": "text/html", "body": {"data": b64("<p>See attached.</p>")}},
            ],
        },
        {
            "mimeType": "application/pdf",
            "filename": "report.pdf",
            "body": {"attachmentId": "att-1", "size": 1024},
        },
    ],
}

# Attachment only — no body part at all.
ATTACHMENT_ONLY = {
    "mimeType": "multipart/mixed",
    "headers": headers(From="Scanner <scan@example.com>", Subject="Scanned doc"),
    "parts": [
        {
            "mimeType": "image/png",
            "filename": "scan.png",
            "body": {"attachmentId": "att-2", "size": 2048},
        }
    ],
}

# An attached .txt file must not be mistaken for the body.
ATTACHED_TEXT_FILE = {
    "mimeType": "multipart/mixed",
    "headers": headers(From="Bot <bot@example.com>", Subject="Logs"),
    "parts": [
        {
            "mimeType": "text/plain",
            "filename": "log.txt",
            "body": {"attachmentId": "att-3", "data": b64("THIS IS AN ATTACHMENT")},
        }
    ],
}

# RFC 2047 encoded subject/sender, plus a header we do not keep.
ENCODED_HEADERS = {
    "mimeType": "text/plain",
    "headers": headers(
        From="=?UTF-8?B?SsO2cmcgTcO8bGxlcg==?= <joerg@example.de>",
        Subject="=?UTF-8?B?RsO8ciBkaWNo?=",
        X_Mailer="SomeMailer 1.0",
        precedence="list",  # lowercase on purpose
    ),
    "body": {"data": b64("Hallo")},
}
