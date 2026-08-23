"""Pulling entity mentions out of one email.

DETERMINISTIC FIRST, LLM SECOND — this repo's standing posture, the same shape
as `classification/rules.py` running before `classification/llm_fallback.py`.
The split is not about cost alone: a regex that finds "CS-40350" is right every
time, and asking a model to re-find it introduces a chance of it being wrong
about something that was never in doubt.

    Pass 1  free, exact, no model
            PERSON / ORG from the From, To, Cc and Reply-To headers, and from
            any address written into the subject line.
            CASE / DOCUMENT ids by regex, over the subject and the body.

    Pass 2  one call on the "extract" stage
            PROJECT / DELIVERABLE / TOPIC — the things that have no canonical
            spelling and so cannot be regexed — plus one judgment: of the ids
            pass 1 already found, which is this email actually ABOUT, and which
            is an incidental mention in a signature footer, a quoted ticket
            link, or a "see also".

The model is never asked to re-find an id. It is shown what pass 1 found and
asked only to rank it.

Only kind=BODY chunks reach the model. Quoted reply history is excluded
deliberately: a case id that appears only inside a quote belongs to whoever
wrote the original message, and attributing it to the replier is how a graph
grows edges that were never there.

One corpus-shaped decision worth stating, because it looks like a special
case and is not. Addresses found in the *subject line* are extracted as
mentions, not just addresses in headers. In real mail that is a rare no-op. In
the corpus this runs against, every message arrives through one relay account,
so `raw.sender` is the same string 161 times out of 163 and the actual
correspondent is written into the subject as "[h.villalobos@stridecore.com]".
Reading only the headers there yields exactly one PERSON node for the whole
corpus and a graph with nothing to correlate. Scanning the subject for
addresses is a general rule that costs nothing when there are none.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from models.schema import Chunk, ChunkKind, EntityKind, Mention, MentionSource, RawEmail

from .normalize import (
    normalize_address,
    normalize_id,
    normalize_name,
    parse_provisional,
    provisional_id,
)

log = logging.getLogger(__name__)

# --- free-mail domains ----------------------------------------------------
# An ORG node derived from "gmail.com" is 40 unrelated people in one bucket:
# it would give the graph a hub that every walk passes through and that means
# nothing. Personal-mail domains are dropped rather than turned into an org.
FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "msn.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "aol.com",
        "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
        "pm.me", "gmx.com", "gmx.de", "mail.com", "zoho.com", "yandex.com",
        "fastmail.com", "hey.com", "qq.com", "163.com", "126.com",
    }
)

# --- id patterns ----------------------------------------------------------

_RE_PREFIX = re.compile(r"^\s*(?:(?:re|fwd|fw|aw|sv)\s*:\s*)+", re.I)

# "CS-40350", "RMA-2026-0447", "HW-1187". The optional second group is what
# keeps RMA-2026-0447 from being truncated to RMA-2026.
_ID_DASHED = re.compile(r"\b([A-Z]{2,10}-\d{1,6}(?:-\d{1,6})?)\b")
# "#4471". Three digits minimum, so "#1" and "#22" in prose are not ids.
_ID_HASH = re.compile(r"(#\d{3,})")

# Prefixes whose meaning is unambiguous by convention.
#
# Everything else defaults to DOCUMENT, not CASE, and that default was chosen
# from real output. Defaulting to CASE produced 68 CASE nodes of which 35
# appeared in exactly one email — because a serial number ("SN-4400-2283", 69
# body-only appearances in this corpus), a part number, an account number and
# an internal reference all match the same `[A-Z]{2,10}-\d+` shape as a ticket
# id. Those became one-email "cases" that the fragmentation check then reported
# as a resolution problem, which it was not.
#
# DOCUMENT is the honest default: an identifier is a reference until something
# says otherwise. Pass 2 promotes the real cases, which is a judgment the model
# is already being asked to make in the same call.
_DOCUMENT_PREFIXES = frozenset(
    {"INV", "PO", "ORD", "RMA", "QUO", "QT", "SO", "DN", "AP", "AR", "CR", "GRN",
     "SN", "SKU", "ACCT", "MAT", "LOT", "BOL", "AWB", "PN"}
)
_CASE_PREFIXES = frozenset(
    {"CASE", "CS", "SUP", "SUPPORT", "INC", "TICKET", "TKT", "REQ", "ISSUE", "BUG", "SR"}
)


def _id_kind(token: str) -> EntityKind:
    prefix = token.split("-", 1)[0].upper()
    if prefix in _CASE_PREFIXES:
        return EntityKind.CASE
    if prefix in _DOCUMENT_PREFIXES:
        return EntityKind.DOCUMENT
    # "#4471" and the like: a bare number is how incident trackers write a
    # case, and no other kind of reference uses that form.
    if token.startswith("#"):
        return EntityKind.CASE
    return EntityKind.DOCUMENT


def strip_reply_prefixes(subject: str) -> str:
    """"Re: Re: Fwd: [CS-40350] ..." -> "[CS-40350] ...".

    Ids live past the prefixes — in this corpus 84 of 163 subjects carry two
    or more — so a scan that starts at character zero and stops early finds
    nothing.
    """
    return _RE_PREFIX.sub("", subject or "").strip()


def find_ids(text: str) -> List[Tuple[str, EntityKind]]:
    """Every machine id in `text`, in first-appearance order, deduplicated."""
    found: List[Tuple[str, EntityKind]] = []
    seen: Set[str] = set()
    for pattern in (_ID_DASHED, _ID_HASH):
        for match in pattern.finditer(text or ""):
            token = match.group(1)
            key = normalize_id(token)
            if key and key not in seen:
                seen.add(key)
                found.append((token, _id_kind(token)))
    return found


_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def find_addresses(text: str) -> List[str]:
    """Bare lowercased addresses in `text`, first-appearance order."""
    out: List[str] = []
    for match in _ADDRESS.finditer(text or ""):
        address = match.group(0).lower().rstrip(".")
        if address not in out:
            out.append(address)
    return out


def _display_name(value: str) -> Optional[str]:
    """The "Display Name" half of "Display Name <addr@host>", if present."""
    value = (value or "").strip()
    if "<" in value and value.endswith(">"):
        name = value[: value.index("<")].strip().strip('"').strip()
        return name or None
    return None


def _org_from_domain(address: str) -> Optional[Tuple[str, str]]:
    """(canonical org name, domain) for a work address, or None for free mail."""
    _, _, domain = address.partition("@")
    domain = domain.lower().strip(".")
    if not domain or domain in FREEMAIL_DOMAINS:
        return None
    label = domain.split(".")[0]
    if len(label) < 2:
        return None
    return label, domain


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return None


# --- pass 1: deterministic ------------------------------------------------

_PARTICIPANT_HEADERS = ("From", "To", "Cc", "Bcc", "Reply-To")


def _mention(
    email_id: str,
    kind: EntityKind,
    key: str,
    span_text: str,
    source: MentionSource,
    chunk_id: Optional[str] = None,
    confidence: float = 1.0,
) -> Mention:
    return Mention(
        email_id=email_id,
        entity_id=provisional_id(kind, key),
        span_text=span_text,
        chunk_id=chunk_id,
        confidence=confidence,
        source=source,
    )


def extract_deterministic(
    raw: RawEmail,
    chunks: Sequence[Chunk] = (),
) -> List[Mention]:
    """Pass 1 — no model, no network, exact.

    PERSON entities key on the bare address, never the display name: the same
    human arrives as "Sam", "Sam Shah", and "S. Shah" across three messages,
    and keying on any of those spellings creates three nodes for one person.
    The display name becomes the canonical name and, later, an alias.
    """
    mentions: List[Mention] = []
    seen: Set[str] = set()

    def add(kind: EntityKind, key: str, span: str, source: MentionSource,
            chunk_id: Optional[str] = None) -> None:
        if not key:
            return
        dedupe_key = "{0}|{1}|{2}".format(kind.value, key, chunk_id or "")
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        mentions.append(_mention(raw.email_id, kind, key, span, source, chunk_id))

    subject = strip_reply_prefixes(raw.subject or "")

    # People and orgs from the envelope.
    raw_participants = [raw.sender or ""] + list(raw.recipients or [])
    for header in _PARTICIPANT_HEADERS:
        value = _header(raw.headers, header)
        if value:
            raw_participants.extend(part for part in value.split(",") if part.strip())

    for value in raw_participants:
        address = normalize_address(value)
        if not address or "@" not in address:
            continue
        add(EntityKind.PERSON, address, _display_name(value) or address,
            MentionSource.HEADER)
        org = _org_from_domain(address)
        if org:
            name, domain = org
            add(EntityKind.ORG, normalize_name(name, EntityKind.ORG), name,
                MentionSource.HEADER)

    # Addresses written into the subject line — see the module docstring.
    for address in find_addresses(subject):
        add(EntityKind.PERSON, address, address, MentionSource.REGEX)
        org = _org_from_domain(address)
        if org:
            name, _ = org
            add(EntityKind.ORG, normalize_name(name, EntityKind.ORG), name,
                MentionSource.REGEX)

    # Ids, from the subject and from the body chunks (never quoted text).
    for token, kind in find_ids(subject):
        add(kind, normalize_id(token), token, MentionSource.REGEX)
    for chunk in chunks:
        if chunk.kind != ChunkKind.BODY:
            continue
        for token, kind in find_ids(chunk.text):
            add(kind, normalize_id(token), token, MentionSource.REGEX, chunk.chunk_id)

    return mentions


# --- pass 2: one model call -----------------------------------------------

SYSTEM_PROMPT = (
    "You read one work email and name the things it is about that a regular "
    "expression cannot find: projects, deliverables, and topics.\n"
    "\n"
    "A PROJECT is a named, longer-lived body of work that outlives any single "
    "thread (a product line, a programme, a named initiative, a named "
    "customer engagement).\n"
    "A DELIVERABLE is a concrete artifact or milestone someone owes someone "
    "else (a firmware release, a sample kit, a signed contract, a shipment, "
    "a report).\n"
    "A TOPIC is the recurring subject matter this email would be filed under.\n"
    "\n"
    "Rules. Name only things this email genuinely refers to; do not infer, "
    "generalize, or invent. Use the shortest name a colleague would "
    "recognize, spelled the way the email spells it. Do not return people, "
    "companies, email addresses, or ticket and invoice identifiers — those "
    "are already extracted separately, and repeating them here creates "
    "duplicates. Return an empty list rather than a weak guess.\n"
    "\n"
    "You are also given the identifiers already found in this email, and you "
    "answer two questions about them.\n"
    "\n"
    "First, which of them is the email actually ABOUT? An identifier is "
    "incidental when it appears only in a signature footer, a boilerplate "
    "reference, a \"see also\", or a list of unrelated past tickets. An "
    "identifier in the subject line is almost always the real subject.\n"
    "\n"
    "Second, which of them name a CASE — a unit of work somebody is handling, "
    "such as a support ticket, an incident, a JIRA issue, or a service "
    "request? Identifiers that are NOT cases are references: product codes and "
    "SKUs, serial numbers, part and material numbers, account numbers, lot "
    "numbers, purchase orders, invoices, and tracking numbers. A serial number "
    "looks much like a ticket id and is not one, so judge from how the email "
    "uses it, not from its shape.\n"
    "\n"
    "Give your reasoning first, then the answer."
)

# Field order is load-bearing. Under constrained JSON decoding the model emits
# fields in declaration order, so `reason` MUST come before the answers —
# otherwise the answer is committed first and the reasoning merely rationalizes
# it. See the comment at scoring/score.py:92. Do not "tidy" this order.
# maxLength on every string is enforced structurally by constrained decoding,
# which makes a repetition loop inside a string impossible rather than merely
# discouraged.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "maxLength": 400},
        "projects": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 60},
        },
        "deliverables": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 60},
        },
        "topics": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 40},
        },
        "primary_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 24},
        },
        "case_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 24},
        },
        "confidence": {"type": "number"},
    },
    "required": ["reason", "projects", "deliverables", "topics", "primary_ids",
                 "case_ids"],
    "additionalProperties": False,
}

# Character budget for the body text shown to the model. Extraction reads the
# top of a message, where the ask lives; the whole 16 KB tail buys little and
# is paid for on every one of ~160 emails.
MAX_BODY_CHARS = 4000

# What an id the model did NOT call primary is worth. Not zero — the mention is
# real and regex-certain, it is just not what this email is about, and
# consolidation weights edges by confidence.
_INCIDENTAL_CONFIDENCE = 0.4

_LLM_KINDS = (
    ("projects", EntityKind.PROJECT),
    ("deliverables", EntityKind.DELIVERABLE),
    ("topics", EntityKind.TOPIC),
)


def _default_model() -> str:
    from llm.client import model_for

    return model_for("extract")


def _get_default_client() -> Any:
    from llm.client import get_client

    return get_client("extract")


def build_user_message(
    raw: RawEmail,
    chunks: Sequence[Chunk],
    ids: Sequence[Tuple[str, EntityKind]],
    subject_ids: Sequence[str],
) -> str:
    """The prompt body: subject, BODY chunks only, and pass 1's ids."""
    body = "\n\n".join(c.text for c in chunks if c.kind == ChunkKind.BODY)
    lines = ["Subject: {0}".format(strip_reply_prefixes(raw.subject or "") or "(none)")]
    if ids:
        # Marking which ids came from the subject gives the model the strongest
        # available signal for the primary-versus-incidental call, instead of
        # making it guess from body position alone.
        rendered = ", ".join(
            "{0}{1}".format(token, " (in subject)" if token in subject_ids else "")
            for token, _ in ids
        )
        lines.append("Identifiers already found in this email: {0}".format(rendered))
    lines.append("")
    lines.append("Body:")
    lines.append(body[:MAX_BODY_CHARS] if body.strip() else "(no body text)")
    return "\n".join(lines)


def extract_entities(
    raw: RawEmail,
    chunks: Sequence[Chunk] = (),
    client: Optional[Any] = None,
) -> List[Mention]:
    """Every mention in one email: pass 1, then one model call for pass 2.

    `chunks` should be this email's chunks; non-BODY ones are ignored rather
    than rejected, so a caller may pass all of them. When there is no body
    text to reason about the model call is skipped entirely — there is nothing
    for it to read, and it would still cost a call.
    """
    deterministic = extract_deterministic(raw, chunks)

    body_chunks = [c for c in chunks if c.kind == ChunkKind.BODY]
    if not any((c.text or "").strip() for c in body_chunks):
        return deterministic

    subject = strip_reply_prefixes(raw.subject or "")
    subject_ids = [token for token, _ in find_ids(subject)]
    ids = find_ids(subject + "\n" + "\n".join(c.text for c in body_chunks))

    if client is None:
        client = _get_default_client()

    response = client.messages.create(
        model=_default_model(),
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": build_user_message(raw, body_chunks, ids, subject_ids),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
    )
    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)

    confidence = _clamp(data.get("confidence"), default=0.8)
    llm_mentions = _mentions_from_llm(raw, data, confidence)
    judged = _apply_id_judgment(
        deterministic,
        primary_ids=data.get("primary_ids") or [],
        case_ids=data.get("case_ids") or [],
    )
    return judged + llm_mentions


def _clamp(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _mentions_from_llm(raw: RawEmail, data: Dict[str, Any], confidence: float) -> List[Mention]:
    """PROJECT / DELIVERABLE / TOPIC mentions from the model's answer.

    Anything the model returned that is really an address or an id is dropped:
    the prompt forbids them, they are already extracted exactly, and letting a
    second spelling of the same thing through is how one entity becomes two.
    """
    out: List[Mention] = []
    seen: Set[str] = set()
    for field, kind in _LLM_KINDS:
        for span in data.get(field) or []:
            span = str(span).strip()
            if not span or "@" in span or find_ids(span):
                continue
            key = normalize_name(span, kind)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(
                _mention(
                    raw.email_id, kind, key, span, MentionSource.LLM,
                    confidence=confidence,
                )
            )
    return out


def _apply_id_judgment(
    mentions: Sequence[Mention],
    *,
    primary_ids: Sequence[str],
    case_ids: Sequence[str],
) -> List[Mention]:
    """Apply pass 2's two judgments to pass 1's id mentions.

    `primary_ids` downweights the incidental ones. The mention is kept either
    way — a regex match is a fact — but an id that appears only in a footer must
    not carry the same weight into the graph as the case the email is about.

    `case_ids` re-kinds an id the regex could only guess at. Only ids that
    landed on the DOCUMENT default are eligible for promotion: a known case
    prefix is already certain, and a known invoice or purchase-order prefix is
    not something a model should be able to overrule. Demotion is not offered
    for the same reason — this corrects the default, it does not relitigate the
    conventions.
    """
    from dataclasses import replace

    primary = {normalize_id(str(token)) for token in primary_ids}
    cases = {normalize_id(str(token)) for token in case_ids}

    out: List[Mention] = []
    for mention in mentions:
        if mention.source != MentionSource.REGEX or not find_ids(mention.span_text):
            out.append(mention)
            continue
        key = normalize_id(mention.span_text)
        parsed = parse_provisional(mention.entity_id)
        if (
            parsed is not None
            and parsed[0] == EntityKind.DOCUMENT
            and key in cases
            and _id_kind(mention.span_text) == EntityKind.DOCUMENT
            and mention.span_text.split("-", 1)[0].upper() not in _DOCUMENT_PREFIXES
        ):
            mention = replace(
                mention, entity_id=provisional_id(EntityKind.CASE, parsed[1])
            )
        if primary and key not in primary:
            mention = replace(mention, confidence=_INCIDENTAL_CONFIDENCE)
        out.append(mention)
    return out
