"""Pure functions turning a Gmail API message resource into a `RawEmail`.

No network, no I/O — everything here is deterministic and unit-testable, which
is why the Gmail-specific messiness (base64url bodies, nested multiparts,
RFC 2047 header encoding, HTML-only mail) is quarantined in this module.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .models import KEPT_HEADERS, READ, UNREAD, RawEmail

# Tags whose text content is markup/metadata, never readable body text.
_SKIP_CONTENT_TAGS = frozenset({"script", "style", "head", "title", "noscript"})

# Tags that imply a line break in the rendered output.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "br", "div", "dd", "dl", "dt",
        "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "ol", "p", "pre", "section", "table", "tbody", "td",
        "tfoot", "th", "thead", "tr", "ul",
    }
)

# Header names that may carry RFC 2047 encoded words and are meant for humans.
# The automation headers Track B parses are deliberately left byte-for-byte.
_DECODABLE_HEADERS = frozenset({"From", "To", "Cc", "Reply-To", "Subject"})

# Unicode spaces that `str.strip` ignores but that render as blank: NBSP,
# the U+2000-200A range (includes figure space), narrow/medium math spaces,
# and the ideographic space.
_SPACES = re.compile(
    r"[ \t\r\f\v\u00a0\u2000-\u200a\u202f\u205f\u3000]+"
)
_BLANK_LINES = re.compile(r"\n{3,}")

# Zero-width and invisible characters. Marketing mail pads its preheader with
# runs of these so the inbox preview looks clean; left in, they are pure noise
# in the stored body and waste downstream LLM tokens. Removed outright rather
# than collapsed to a space, since they occupy no visual width.
_INVISIBLE = re.compile(r"[\u200b-\u200d\u00ad\u034f\u2060\ufeff]")

# A `selector { prop: value }` block sitting in text. Some senders paste their
# stylesheet into the text/plain part; there is no <style> tag to key off, so
# the block is matched structurally. Requires a colon inside the braces so
# prose containing braces is left alone.
_CSS_BLOCK = re.compile(r"[^\n{}]*\{[^{}]*:[^{}]*\}")

# Markup that means a "plain text" part is not actually plain text.
_HTML_TAG = re.compile(
    r"</?(?:a|b|body|br|div|em|font|h[1-6]|head|hr|html|i|img|li|ol|p|span"
    r"|strong|style|table|tbody|td|th|thead|tr|u|ul)\b[^>]*>",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """Collects the readable text of an HTML document.

    This extracts text rather than sanitizing-and-rendering markup, so there is
    no injection surface: tags, attributes, scripts, and styles are dropped
    outright and only character data survives. `convert_charrefs` handles
    entity unescaping. `HTMLParser` is tolerant of malformed markup, which
    matters because a lot of real email HTML is malformed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._suppress_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP_CONTENT_TAGS:
            self._suppress_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_CONTENT_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces and blank lines, and trim each line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def html_to_text(html: str) -> str:
    """Strip HTML down to readable plain text."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # HTMLParser is tolerant, but never let one malformed message abort a
        # whole ingestion run — keep whatever was parsed before the failure.
        pass
    return normalize_whitespace(parser.text())


def decode_base64url(data: Optional[str]) -> str:
    """Decode Gmail's base64url body payload into text.

    Gmail omits padding, and bodies are not guaranteed to be valid UTF-8, so
    both are handled leniently rather than raising mid-run.
    """
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _decode_mime_header(value: str) -> str:
    """Decode RFC 2047 encoded words (`=?UTF-8?B?...?=`) in a header value."""
    if "=?" not in value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def extract_headers(payload: Dict[str, Any]) -> Dict[str, str]:
    """Pull the `KEPT_HEADERS` allowlist off a payload, preserving casing.

    Gmail's header casing varies by sender, so matching is case-insensitive but
    the canonical name from `KEPT_HEADERS` is used as the stored key — that way
    downstream code can do `headers["List-Unsubscribe"]` without guessing.
    """
    canonical = {name.lower(): name for name in KEPT_HEADERS}
    out: Dict[str, str] = {}
    for header in payload.get("headers", []) or []:
        name = canonical.get(str(header.get("name", "")).lower())
        if name is None or name in out:
            continue
        value = str(header.get("value", ""))
        out[name] = _decode_mime_header(value) if name in _DECODABLE_HEADERS else value
    return out


def get_header(payload: Dict[str, Any], name: str) -> str:
    """Case-insensitive lookup of a single header, decoded if human-facing."""
    wanted = name.lower()
    for header in payload.get("headers", []) or []:
        if str(header.get("name", "")).lower() == wanted:
            value = str(header.get("value", ""))
            return _decode_mime_header(value) if name in _DECODABLE_HEADERS else value
    return ""


# --- relay-tag senders -----------------------------------------------------
# Some mail reaches an inbox through a single relay/forwarding account, with
# the real correspondent written into the front of the subject as
# "[a.person@example.com]". Reading only the From: header there collapses
# every message onto one sender, which silently breaks anything keyed on who
# wrote the mail: no-reply detection, VIP scoring, per-sender feedback priors,
# and the sender shown to the model in every prompt.
#
# The same reasoning is already written into context/extract.py, which scans
# subjects for addresses so the entity graph does not collapse to one PERSON
# node. This is that rule applied at the ingestion boundary instead of in one
# consumer, so every downstream stage sees the real sender without knowing
# the convention exists.
#
# Deliberately conservative: the tag must sit at the very start (after any
# Re:/Fwd: chain) and must parse as an address. A subject like
# "[CS-40218] Escalated to Severity 1" is left alone, and ordinary mail with
# no tag is returned unchanged, so this costs nothing when it does not apply.
_RELAY_TAG_RE = re.compile(
    r"^(?P<prefix>(?:\s*(?:re|fwd|fw)\s*:\s*)*)"
    r"\[\s*(?P<addr>[^\]\s@]+@[^\]\s@]+\.[^\]\s@]+)\s*\]\s*"
    r"(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def split_relay_tag(subject: str) -> Tuple[Optional[str], str]:
    """Split a leading "[addr]" relay tag off a subject line.

    Returns (relayed_sender_address, subject_without_tag). When there is no
    tag — the ordinary case for real mail — returns (None, subject) so the
    caller can treat tagged and untagged mail identically. Any Re:/Fwd:
    prefix chain is preserved on the returned subject, since it carries
    thread depth that scoring and the UI both read.
    """
    match = _RELAY_TAG_RE.match(subject or "")
    if match is None:
        return None, subject
    rest = match.group("rest").strip()
    if not rest:
        # A subject that is *only* a tag would otherwise become empty; keep
        # the original rather than hand downstream stages a blank subject.
        return match.group("addr").strip(), subject
    return match.group("addr").strip(), (match.group("prefix") + rest).strip()


def _is_attachment(part: Dict[str, Any]) -> bool:
    body = part.get("body", {}) or {}
    return bool(part.get("filename")) or bool(body.get("attachmentId"))


def _collect_bodies(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Walk the MIME tree, returning (plain_text_parts, html_parts).

    Attachments are skipped: an attached .txt or .html file is not body text.
    """
    plains: List[str] = []
    htmls: List[str] = []

    def walk(part: Dict[str, Any]) -> None:
        if not isinstance(part, dict):
            return
        for child in part.get("parts") or []:
            walk(child)
        if _is_attachment(part):
            return
        mime = str(part.get("mimeType", "")).lower()
        data = (part.get("body", {}) or {}).get("data")
        if not data:
            return
        if mime == "text/plain":
            plains.append(decode_base64url(data))
        elif mime == "text/html":
            htmls.append(decode_base64url(data))

    walk(payload)
    return plains, htmls


def looks_like_html(text: str) -> bool:
    """Whether text declared as `text/plain` actually contains markup.

    A MIME type is a claim by the sender, not a guarantee — the same reason
    `internalDate` is preferred over the `Date:` header. In practice a
    meaningful share of bulk senders put full HTML, or a bare stylesheet, in
    their text/plain part.
    """
    return bool(_HTML_TAG.search(text) or _CSS_BLOCK.search(text))


def clean_plain_text(text: str) -> str:
    """Normalize a `text/plain` part, repairing markup the sender left in it."""
    if not text:
        return ""
    if looks_like_html(text):
        # Strip tags first, then any stylesheet left behind outside a <style>.
        text = html_to_text(text)
        text = _CSS_BLOCK.sub("", text)
    else:
        # Genuine plain text still routinely carries `&nbsp;` / `&amp;`.
        text = unescape(text)
    return normalize_whitespace(text)


def extract_body(payload: Dict[str, Any]) -> str:
    """Best plain-text rendering of a message body.

    Prefers a real `text/plain` part; falls back to stripping `text/html`.
    Returns "" for attachment-only or bodyless messages rather than raising —
    Phase 8 hardens the downstream handling of those, ingestion just records
    them faithfully.
    """
    plains, htmls = _collect_bodies(payload or {})
    if plains:
        text = clean_plain_text("\n".join(plains))
        if text:
            return text
    if htmls:
        return html_to_text("\n".join(htmls))
    return ""


def has_attachments(payload: Dict[str, Any]) -> bool:
    """True if any part of the MIME tree is an attachment."""

    def walk(part: Dict[str, Any]) -> bool:
        if not isinstance(part, dict):
            return False
        if _is_attachment(part):
            return True
        return any(walk(child) for child in part.get("parts") or [])

    return walk(payload or {})


def internal_date_to_datetime(internal_date: Any) -> datetime:
    """Gmail `internalDate` (epoch milliseconds) -> timezone-aware UTC datetime.

    `internalDate` is when Gmail received the message. It is preferred over the
    `Date:` header, which is set by the sending client and is frequently wrong
    or missing a usable timezone.

    Returns a `datetime` rather than an ISO string because that is what the
    frozen contract in `models/schema.py` specifies. Serialization back to
    text is `store.py`'s concern, not the parser's.
    """
    try:
        millis = int(internal_date)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def split_recipients(payload: Dict[str, Any]) -> List[str]:
    """Every address this message was addressed to, To: and Cc: combined.

    Required by the frozen contract and used by Track B's direct-vs-CC
    importance signal. Addresses are kept as written (display name included)
    to match `sender`; callers that need the bare address parse it themselves.
    """
    combined = ", ".join(
        value for value in (get_header(payload, "To"), get_header(payload, "Cc")) if value
    )
    return [part.strip() for part in combined.split(",") if part.strip()]


def to_raw_email(message: Dict[str, Any]) -> RawEmail:
    """Map a `users.messages.get(format="full")` resource to a `RawEmail`."""
    payload = message.get("payload", {}) or {}
    label_ids = list(message.get("labelIds") or [])
    headers = extract_headers(payload)

    # Resolve the sender once, here, so no downstream stage has to know that
    # relay-tagged mail exists. The envelope sender is preserved in the
    # headers dict (already a free-form blob, so this needs no schema change)
    # because it is still the address the message was actually delivered from.
    envelope_sender = get_header(payload, "From")
    subject = get_header(payload, "Subject")
    relayed_sender, subject = split_relay_tag(subject)
    if relayed_sender is not None:
        headers["X-Envelope-From"] = envelope_sender

    return RawEmail(
        email_id=str(message.get("id", "")),
        thread_id=str(message.get("threadId", "")),
        sender=relayed_sender or envelope_sender,
        subject=subject,
        recipients=split_recipients(payload),
        body=extract_body(payload),
        snippet=normalize_whitespace(str(message.get("snippet", ""))),
        received_at=internal_date_to_datetime(message.get("internalDate")),
        read_status=UNREAD if "UNREAD" in label_ids else READ,
        label_ids=label_ids,
        headers=headers,
        has_attachments=has_attachments(payload),
    )
