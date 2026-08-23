"""Splitting one email into embeddable spans — and getting the quoted reply
history out of the way first.

Quote stripping is the load-bearing part of this module, not tidiness. An
unstripped reply chain repeats most of a thread inside every message in it,
and that has two concrete costs:

  - Embeddings collapse. Ten messages that each quote the nine before them
    are ~90% identical text, so every pair scores ~0.95 cosine and the vector
    channel can no longer tell them apart. Retrieval degrades to noise.
  - Entities get misattributed. A name or case ID that appears only inside
    quoted history belongs to whoever wrote the original message, not to the
    person who happened to reply below it.

So the body is cut at the quote boundary *before* chunking. Nothing is
discarded, though: the quoted and signature regions are emitted as their own
Chunk rows with kind=QUOTED / kind=SIGNATURE, so `context.cli chunks` can
show what was removed and nothing is silently lost. Only kind=BODY is
embedded and mined for entities.

What the real corpus actually looks like, which is what these heuristics are
shaped around rather than a textbook example:

    ...end of the new message.

    --                                      <- signature delimiter
    Aleksandra Petrova
    Firmware Lead | StrideCore Technologies
    s.petrova@stridecore.com | +1 503-555-0155

    On Sat, Aug 22, 2026 11:54 PM, Inconspicuous Turtle <
    boredomcure2020@gmail.com> wrote:        <- attribution, WRAPPED over 2 lines

    > Sasha, Hector, Ronith —
    > ...

Two details that a naive implementation gets wrong on this mail:

1. The attribution line wraps. `ingestion.parse.normalize_whitespace` trims
   every line, so a long "On ... wrote:" arrives split across two or three
   lines and a line-anchored `$` regex never matches it. The pattern here
   spans newlines deliberately.
2. `>` does not always mean reply history. In this corpus 86 messages contain
   `>`-prefixed lines, but four of them are machine-generated notifications
   using `>` as a block quote *mid-body* — a PagerDuty resolution note with
   RELATED and TIMELINE sections underneath it. Cutting at the first `>` would
   throw away the real content. A `>` run is treated as history only when an
   attribution introduces it, or when it runs to the end of the message.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from models.schema import Chunk, ChunkKind, RawEmail

# --- quote boundary --------------------------------------------------------

# "On <date>, <someone> wrote:". re.S because the line wraps (see above); the
# lookahead requires a digit on the first line, which every real attribution
# has (a date) and which stops a body sentence starting with "On " from
# matching something 200 characters later.
_ON_WROTE = re.compile(r"^On\s(?=[^\n]*\d).{3,240}?\bwrote:", re.M | re.S)

# "<Name> wrote:" on its own line, the form Gmail uses when it has no date.
_NAME_WROTE = re.compile(r"^.{0,120}?\bwrote:\s*$", re.M)

_DIVIDERS = (
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.M | re.I),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}", re.M | re.I),
    re.compile(r"^\s*_{5,}\s*$", re.M),          # Outlook's rule above headers
    # An Outlook/Exchange quoted header block: From: then Sent:/Date: under it.
    re.compile(r"^\s*From:\s*\S.*\n\s*(?:Sent|Date|To):\s", re.M),
)

# How much unquoted text may follow a `>` run before we stop believing the run
# is trailing reply history and treat it as a mid-body block quote instead.
_TRAILING_TOLERANCE = 200


def _quote_marker_start(text: str) -> Optional[int]:
    """Offset of the earliest attribution line or quote divider, if any."""
    starts = [m.start() for m in (_ON_WROTE.search(text), _NAME_WROTE.search(text)) if m]
    for pattern in _DIVIDERS:
        match = pattern.search(text)
        if match:
            starts.append(match.start())
    return min(starts) if starts else None


def _quoted_runs(lines: Sequence[str]) -> List[Tuple[int, int]]:
    """(first, last) line indices of every maximal run of `>`-prefixed lines."""
    runs: List[Tuple[int, int]] = []
    start = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(">"):
            if start is None:
                start = index
        elif start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(lines) - 1))
    return runs


def _history_run_offset(text: str) -> Optional[int]:
    """Character offset where trailing `>` reply history begins, if it does.

    A run qualifies only when little or no real text follows it. That is what
    separates reply history (which runs to the bottom of the message) from a
    block quote a notification put in the middle of its own body.
    """
    lines = text.split("\n")
    # Character offset of the start of each line.
    offsets, running = [], 0
    for line in lines:
        offsets.append(running)
        running += len(line) + 1

    for first, last in _quoted_runs(lines):
        tail = "\n".join(
            line for line in lines[last + 1 :] if not line.lstrip().startswith(">")
        ).strip()
        if len(tail) <= _TRAILING_TOLERANCE:
            return offsets[first]
    return None


def split_quoted(text: str) -> Tuple[str, str]:
    """(kept, quoted). `quoted` is "" when there is no reply history."""
    candidates = [
        offset
        for offset in (_quote_marker_start(text), _history_run_offset(text))
        if offset is not None
    ]
    if not candidates:
        return text, ""
    cut = min(candidates)
    return text[:cut].rstrip(), text[cut:].strip()


# --- signature ------------------------------------------------------------

# The conventional delimiter. Note it is matched WITHOUT a trailing space:
# RFC-style "-- " loses that space to `normalize_whitespace`, which strips
# every line, so anchoring on "-- " would miss all 156 messages in this corpus
# that use it.
_SIG_DELIM = re.compile(r"^\s*(?:--+|__+|—)\s*$", re.M)

# A signature is short. Past this, a "--" is a horizontal rule in the body.
_MAX_SIG_CHARS = 900

_CONTACT_PATTERNS = (
    re.compile(r"\+?\d[\d\s().\-]{7,}\d"),                      # phone number
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                     # email address
    re.compile(r"\bhttps?://|\bwww\."),                         # a link
    re.compile(
        r"\b\d+\s+\w+([\s\w]*)\b(?:St|Street|Ave|Avenue|Dr|Drive|Rd|Road|"
        r"Blvd|Way|Lane|Ln|Suite|Ste|Floor|Fl)\b",
        re.I,
    ),                                                          # street address
    re.compile(
        r"\b(?:CEO|CTO|COO|CFO|VP|Director|Manager|Lead|Head of|Founder|"
        r"Partner|Engineer|Analyst|Coordinator|President|Officer|"
        r"Specialist|Consultant)\b",
        re.I,
    ),                                                          # job title
    re.compile(r"^Sent from my ", re.I),                        # mobile footer
    re.compile(r"\b(?:unsubscribe|opt out|opt-out|you'?re receiving this|"
               r"don'?t want these)\b", re.I),                  # bulk footer
    re.compile(r"^\s*\S.*\|\s*\S", re.M),                       # "Title | Org"
)


def _looks_like_contact(line: str) -> bool:
    return any(pattern.search(line) for pattern in _CONTACT_PATTERNS)


def _contact_block_offset(text: str) -> Optional[int]:
    """Where a trailing block of mostly-contact-details starts, if there is one.

    The fallback for mail with no `--` delimiter. Walks the final paragraphs
    backwards while they keep looking like contact details, and refuses to eat
    a paragraph that reads like prose (long lines, few contact markers) — the
    failure mode to avoid is swallowing the last real point of the message.
    """
    paragraphs = _paragraph_spans(text)
    cut = None
    for start, end in reversed(paragraphs):
        block = text[start:end]
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines or len(block) > 400:
            break
        contactish = sum(1 for line in lines if _looks_like_contact(line))
        # A name on its own line carries no contact marker but is part of the
        # signature; allow it only as a minority of the block.
        if contactish * 2 < len(lines) or contactish == 0:
            break
        if len(text) - start > _MAX_SIG_CHARS:
            break
        cut = start
    return cut


def split_signature(text: str) -> Tuple[str, str]:
    """(kept, signature). `signature` is "" when none was found.

    Both rules run, not just the first that fires. A "--" delimiter often sits
    *below* a hand-typed sign-off ("Best,\nGrant Feldman\nSupply Chain Manager
    | StrideCore\ng.feldman@... | +1 503-555-0138\n\n--\nStrideCore | 4400 SW
    Macadam Ave"), and cutting only at the delimiter leaves that name, title,
    org, address and phone number sitting in a BODY chunk — a block of pure
    boilerplate that gets embedded and mined for entities as though it were
    something the sender said.
    """
    cut = None
    for match in _SIG_DELIM.finditer(text):
        if len(text) - match.start() <= _MAX_SIG_CHARS:
            cut = match.start()
            break               # earliest qualifying delimiter, so nested
            #                     sign-offs below it come along with it
    head = text if cut is None else text[:cut]
    earlier = _contact_block_offset(head)
    if earlier is not None:
        cut = earlier
    if cut is None or cut == 0:
        return text, ""
    return text[:cut].rstrip(), text[cut:].strip()


# --- chunking -------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+|\n")


def _paragraph_spans(text: str) -> List[Tuple[int, int]]:
    """(start, end) offsets of blank-line-separated paragraphs."""
    spans: List[Tuple[int, int]] = []
    position = 0
    for block in re.split(r"\n\s*\n", text):
        start = text.find(block, position)
        if start < 0:                       # pragma: no cover - defensive
            start = position
        spans.append((start, start + len(block)))
        position = start + len(block)
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _split_long(block: str, target_chars: int) -> List[str]:
    """Break one oversized paragraph on sentence boundaries, never mid-sentence."""
    pieces = [p for p in _SENTENCE_END.split(block) if p and p.strip()]
    out: List[str] = []
    current = ""
    for piece in pieces:
        candidate = (current + " " + piece).strip() if current else piece
        if current and len(candidate) > target_chars:
            out.append(current)
            current = piece
        else:
            current = candidate
    if current:
        out.append(current)
    # A single sentence longer than the target stays whole: splitting it would
    # be the mid-sentence break this function exists to prevent.
    return out or [block]


def _tail_overlap(text: str, overlap: int) -> str:
    """The last <=`overlap` chars of `text`, snapped forward to a sentence start.

    Snapping matters: an overlap that begins mid-sentence puts a fragment at
    the top of the next chunk, which is exactly the thing sentence-aware
    splitting was for.
    """
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    boundary = _SENTENCE_END.search(tail)
    if boundary:
        tail = tail[boundary.end() :]
    return tail.strip()


def split_body(text: str, *, target_chars: int = 800, overlap: int = 100) -> List[str]:
    """Paragraph-aligned chunks of about `target_chars`, with carry-over."""
    text = text.strip()
    if not text:
        return []

    blocks: List[str] = []
    for start, end in _paragraph_spans(text):
        block = text[start:end].strip()
        blocks.extend(
            _split_long(block, target_chars) if len(block) > target_chars else [block]
        )

    chunks: List[str] = []
    current = ""
    for block in blocks:
        candidate = (current + "\n\n" + block) if current else block
        if current and len(candidate) > target_chars:
            chunks.append(current)
            carry = _tail_overlap(current, overlap)
            current = (carry + "\n\n" + block) if carry else block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_email(
    raw: RawEmail,
    *,
    target_chars: int = 800,
    overlap: int = 100,
) -> List[Chunk]:
    """Every Chunk for one email, in document order.

    BODY chunks first, then the signature, then the quoted history — which is
    the order they appear in the message. `ord` runs across all kinds, and
    `chunk_id` is derived from (email_id, ord) so re-chunking an email upserts
    over its old rows instead of accumulating duplicates.
    """
    text = (raw.body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    kept, quoted = split_quoted(text)
    kept, signature = split_signature(kept)

    pieces: List[Tuple[str, ChunkKind]] = [
        (body, ChunkKind.BODY) for body in split_body(
            kept, target_chars=target_chars, overlap=overlap
        )
    ]
    # Quoted and signature text is stored so it can be inspected, but it is
    # never embedded or extracted from, so it is chunked only coarsely — one
    # row per target-sized span, no overlap, since nothing searches it.
    if signature:
        pieces += [(s, ChunkKind.SIGNATURE) for s in split_body(
            signature, target_chars=target_chars, overlap=0
        )]
    if quoted:
        pieces += [(q, ChunkKind.QUOTED) for q in split_body(
            quoted, target_chars=target_chars, overlap=0
        )]

    return [
        Chunk(
            chunk_id="{0}:{1}".format(raw.email_id, index),
            email_id=raw.email_id,
            ord=index,
            text=piece,
            kind=kind,
        )
        for index, (piece, kind) in enumerate(pieces)
    ]
