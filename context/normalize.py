"""Turning a surface form into a stable identity key.

Shared by extraction (which produces provisional ids) and resolution (which
turns them into real entity ids), so the two cannot disagree about what
counts as the same thing. Pure string work: no DB, no model, no imports
beyond the stdlib and the schema enums.

The provisional-id convention exists because `Mention` has no `kind` field —
it points at an entity, and an entity carries the kind. Extraction runs before
any entity exists, so a mention leaves that pass holding
`"<kind>:<normalized_key>"` and `context.resolve` rewrites it to a real
entity_id. Encoding the kind in the string keeps `extract_entities ->
list[Mention]` as the frozen contract says while losing nothing resolution
needs; `span_text` carries the display form.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from models.schema import EntityKind

_LEADING_ARTICLES = ("the ", "a ", "an ")

# Kept out of the key so "Project Atlas", "project atlas.", and "the Atlas
# project" all land on the same identity.
_GENERIC_WORDS = (
    "project",
    "programme",
    "program",
    "initiative",
    "case",
    "ticket",
    "incident",
    "issue",
)

_PUNCT = re.compile(r"[^\w\s@.+-]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_TRAILING_JUNK = re.compile(r"[.\-_\s]+$")

# Kinds whose names are common nouns, where a trailing plural is noise
# ("sample kits" and "sample kit" are one deliverable). Never applied to
# PERSON (keyed on an address) or to ids.
_SINGULARIZE_KINDS = (
    EntityKind.ORG,
    EntityKind.PROJECT,
    EntityKind.DELIVERABLE,
    EntityKind.DOCUMENT,
    EntityKind.TOPIC,
)


# Endings where a trailing "s" is part of the word, not a plural. Without
# these, "Atlas" normalizes to "atla" and stops matching itself, and
# "Technologies" becomes "technologie" — which matches neither "technology"
# nor anything else. Over-merging is this layer's primary risk (see
# PHASES-COMPLEX.md §10), so the rule stays conservative: it is better to miss
# a plural and let the embedding rung catch it than to fuse two real entities.
_NOT_PLURAL_ENDINGS = ("ss", "us", "is", "as", "os", "es")


def _singularize(text: str) -> str:
    """Fold a trailing plural, conservatively.

    Deliberately not a real inflector — it only has to make two spellings of
    one phrase agree. Two rules: "-ies" becomes "-y" ("technologies" ->
    "technology", which is what the singular is actually spelled), and a plain
    trailing "s" is dropped ("sample kits" -> "sample kit"). Everything else is
    left exactly as written.
    """
    words = text.split()
    if not words:
        return text
    last = words[-1]
    if len(last) > 4 and last.endswith("ies"):
        words[-1] = last[:-3] + "y"
    elif len(last) > 3 and last.endswith("s") and not last.endswith(_NOT_PLURAL_ENDINGS):
        words[-1] = last[:-1]
    return " ".join(words)


def normalize_name(text: str, kind: Optional[EntityKind] = None) -> str:
    """The identity key for a surface form.

    Lowercases, drops punctuation, strips leading articles and the generic
    noun that people attach and drop at random ("the Atlas project" /
    "Atlas"), collapses whitespace, and singularizes a trailing plural for the
    common-noun kinds.
    """
    key = _PUNCT.sub(" ", (text or "").lower())
    key = _WHITESPACE.sub(" ", key).strip()
    for article in _LEADING_ARTICLES:
        if key.startswith(article):
            key = key[len(article) :]
            break
    words = [w for w in key.split() if w not in _GENERIC_WORDS]
    key = " ".join(words) if words else key
    key = _TRAILING_JUNK.sub("", key)
    if kind in _SINGULARIZE_KINDS:
        key = _singularize(key)
    return key


def normalize_id(text: str) -> str:
    """The identity key for a machine id — CS-40350, #4471, RMA-2026-0447.

    Uppercased and stripped of separators so "cs-40350", "CS 40350", and
    "CS40350" agree, which they do in real mail. Kept separate from
    `normalize_name` because an id must never be singularized or have a word
    dropped from it.
    """
    return re.sub(r"[^\w]+", "", (text or "")).upper()


def normalize_address(value: str) -> str:
    """A bare, lowercased email address from a possibly-decorated field.

    Reuses `scoring.signals._addr_only` rather than adding a second address
    parser to the repo — it already handles the "Display Name <addr>" form
    that Gmail's From/To/Cc headers arrive in.
    """
    from scoring.signals import _addr_only

    return _addr_only(value or "")


# --- provisional ids ------------------------------------------------------

_SEPARATOR = ":"


def provisional_id(kind: EntityKind, normalized_key: str) -> str:
    """The placeholder entity_id a mention carries until it is resolved."""
    return "{0}{1}{2}".format(EntityKind(kind).value, _SEPARATOR, normalized_key)


def parse_provisional(entity_id: str) -> Optional[Tuple[EntityKind, str]]:
    """(kind, normalized_key) for a provisional id, or None if it is a real one.

    Real entity ids are opaque and contain no ":", so this doubles as the test
    for "has this mention been resolved yet".
    """
    if _SEPARATOR not in (entity_id or ""):
        return None
    prefix, _, key = entity_id.partition(_SEPARATOR)
    try:
        return EntityKind(prefix), key
    except ValueError:
        return None
