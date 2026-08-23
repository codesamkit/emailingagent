# Module Interfaces — frozen after Phase 0

Function signatures each track builds against. Owned by nobody; changes require
flagging the other two tracks first (see `CONTEXT.md`'s checkpoint rules).

Everyone can build against these signatures with stub/fixture implementations
before the real ones land — that's the point of freezing this early.

All types referenced below (`RawEmail`, `ProcessedEmail`, `CalendarContext`,
`CalendarSlot`, enums) are defined in `models/schema.py`.

---

## Ingestion (Track A) → produces `RawEmail`

```python
# ingestion/fetch.py
def fetch_recent_emails(
    max_results: int = 50,
    page_token: str | None = None,
) -> tuple[list[RawEmail], str | None]:
    """Fetch up to max_results recent Gmail messages, oldest-first pagination
    via page_token. Returns (emails, next_page_token); next_page_token is
    None when there are no more pages. Raises on auth failure; retries with
    backoff internally on rate-limit (429) responses."""

# ingestion/store.py
def store_raw_emails(emails: list[RawEmail]) -> None:
    """Upsert into the raw_email table (models/db.py), keyed by email_id."""
```

## Classification (Track B) → produces `is_no_reply`

```python
# classification/classify.py
def is_no_reply(email: RawEmail) -> tuple[bool, str]:
    """Returns (is_no_reply, reason). reason is a short human-readable
    string either way (e.g. "sender matches no-reply@ pattern" or
    "personal sender, no bulk headers"). Runs the rule-based pass first;
    falls back to an LLM call only when rules are inconclusive."""
```

## Scoring (Track B) → produces `importance_score`

```python
# scoring/score.py
def score_importance(
    email: RawEmail,
    is_no_reply: bool,
) -> tuple[float, ImportanceLevel, str]:
    """Returns (importance_score 0-100, importance_level, justification).
    Computes rule-based signals internally (scoring/signals.py) and passes
    them as context to a single LLM call for the final score+justification."""

# scoring/filters.py
def top_n(emails: list[ProcessedEmail], n: int) -> list[ProcessedEmail]: ...
def urgent_and_unread(emails: list[ProcessedEmail]) -> list[ProcessedEmail]: ...
```

## Summarization (Track B) → produces `summary`

```python
# summarization/summarize.py
def summarize(email: RawEmail) -> str:
    """1-3 sentence factual summary: topic, ask (if any), deadline (if any)."""

# summarization/batch.py
def summarize_batch(emails: list[RawEmail]) -> dict[str, str]:
    """Returns {email_id: summary}. Batches underlying LLM calls for volume;
    same output contract as calling summarize() on each email individually."""
```

## Categorization (Track B) → produces `category`

> Added post-Phase 8 via the append-only schema process: new
> `ProcessedEmail.category` field and new function, no existing signature
> changed.

```python
# classification/categorize.py
def categorize_topic(email: RawEmail) -> str:
    """Free-form topic — what the email is about. A short normalized string
    (lowercase, 1-3 words, e.g. "job application", "order shipping"),
    "other" when nothing usable comes back. Single LLM call, routable
    independently as stage "categorize"."""
```

## Calendar (Track A) → produces `calendar_context`

> **Package is `calendaring/`, not `calendar/`.** A top-level package named
> `calendar` shadows the standard library's `calendar` module, which
> `http.cookiejar` imports (`from calendar import timegm`); that breaks
> `requests` and therefore `google-auth`'s transport. Renamed in Phase 1B.

```python
# calendaring/scheduling_intent.py
def is_scheduling_related(email: RawEmail) -> bool:
    """Cheap keyword/intent check — no Calendar API call. Gate that decides
    whether get_calendar_context/suggest_available_slots are worth calling
    at all for this email. Imported directly by Track C's drafting module."""

# calendaring/context.py
def get_calendar_context(
    range_start: datetime,
    range_end: datetime,
    *,
    service=None,                       # additive: injection for tests
    calendar_ids: list[str] | None = None,   # additive: multi-calendar
    days: int | None = None,            # additive: default-window shorthand
) -> CalendarContext:
    """Free/busy blocks + existing events for the given window. Does not
    set suggested_slots — that's suggest_available_slots' job."""

# calendaring/suggest.py
def suggest_available_slots(
    duration_minutes: int,
    range_start: datetime,
    range_end: datetime,
    working_hours: tuple[int, int] = (9, 17),
    *,
    context: CalendarContext | None = None,  # additive: reuse fetched data
    service=None,
    calendar_ids: list[str] | None = None,
    max_slots: int | None = None,
    include_weekends: bool | None = None,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> list[CalendarSlot]:
    """2-3 proposed open slots within working_hours, given free/busy data
    for the range. Returns [] if nothing fits — caller decides fallback
    messaging (e.g. outline bullet says "no open slots found this week")."""

# calendaring/suggest.py — convenience for the pipeline
def suggest_for_context(
    context: CalendarContext,
    duration_minutes: int | None = None,
    **kwargs,
) -> CalendarContext:
    """Fills context.suggested_slots in place and returns it."""
```

### Notes on the additive parameters

All the keyword-only parameters above were **added** in Phase 1B; the
positional signatures are unchanged, so anything built against the Phase 0
contract still works.

The one that matters for Track C is `context=`. The pipeline has already
populated `ProcessedEmail.calendar_context` by the time drafting runs, so
passing it reuses that data instead of paying for a second `freebusy.query`
per email:

```python
slots = suggest_available_slots(30, context=processed.calendar_context)
```

Working hours are interpreted in the **calendar's own timezone**, not UTC.

## Calendar event proposal & creation (Track A) → produces `proposed_event`

```python
# calendaring/propose.py
def extract_proposed_event(
    processed: ProcessedEmail,
    raw: RawEmail,
    *,
    client=None,             # additive: injection for tests
    tz: str | None = None,   # additive: override the interpretation timezone
) -> tuple[ProposedEvent | None, ProposedEventStatus]:
    """Code-level gate BEFORE any LLM call:
      - processed.is_scheduling_related != True -> (None, ProposedEventStatus.NONE)
      - else -> one JSON-schema-constrained LLM call; if the email pins down
        a specific date/time, (ProposedEvent, ProposedEventStatus.SUGGESTED);
        if it's only a vague availability ask, (None, ProposedEventStatus.NONE).
    Never calls the Calendar API and never writes anything — extraction only.
    Called by the pipeline's propose_event stage, after the calendar stage."""

# calendaring/events.py
def create_event(
    proposed: ProposedEvent,
    service=None,               # additive: injection for tests
    calendar_id: str = "primary",
    timezone_name: str | None = None,
) -> ProposedEvent:
    """Calls events.insert. Returns a copy of `proposed` with google_event_id
    set on success, or error set on ANY failure (API error, missing/expired
    token, missing client secrets) — never raises. The ONLY function in the
    repo that writes to Calendar. Called from exactly one place: api/main.py's
    approve endpoint, in direct response to a human clicking Approve in the
    review UI. Never called from the pipeline or any batch job — see
    PHASES.md Phase 1B and calendaring/config.py's WRITE_SCOPE comment for
    why that separation is load-bearing, not incidental. When `service` isn't
    injected, credential lookup is non-interactive (allow_interactive=False)
    — this runs inside a synchronous HTTP request, so a missing token must
    fail fast with a message telling the operator to run
    `python -m calendaring.cli auth`, not block the request on a browser
    consent popup."""
```

> Added post-Phase 8, append-only: `update_event`/`delete_event` in
> `calendaring/events.py`, no existing signature changed. Called only from
> `api/main.py`'s `/calendar-event/update` and `/calendar-event/cancel`
> routes — explicit, human-triggered requests, never the pipeline. Only
> reachable when `proposed_event_status == APPROVED`, i.e. after `create_event`
> has already put a real event on Calendar.

```python
# calendaring/events.py
def update_event(
    event_id: str,
    *,
    summary: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
    timezone_name: str | None = None,
    service=None,
) -> dict:
    """events.patch — partial update. Renaming is summary=...; rescheduling
    is start=.../end=...; an omitted field is left as-is on the event.
    Unlike create_event, this RAISES on failure: the caller already has its
    own try/except, and a failed edit must not flip proposed_event_status
    away from APPROVED — the event still exists with its old data."""

def delete_event(
    event_id: str,
    *,
    calendar_id: str = "primary",
    service=None,
) -> None:
    """events.delete. A 404/410 (already gone) is swallowed, not raised —
    otherwise raises like update_event."""
```

## Drafting (Track C) → produces `reply_outline`

```python
# drafting/outline.py
def generate_reply_outline(
    processed: ProcessedEmail,
    raw: RawEmail,
) -> tuple[list[str] | None, ReplyOutlineStatus]:
    """Code-level gate BEFORE any LLM call:
      - read_status != READ            -> (None, ReplyOutlineStatus.NONE)
      - is_no_reply == True            -> (None, ReplyOutlineStatus.NOT_APPLICABLE)
      - else                           -> generate outline bullets,
                                           (bullets, ReplyOutlineStatus.SUGGESTED)
    When processed.is_scheduling_related is True, calls
    calendaring.suggest.suggest_available_slots (via processed.calendar_context,
    already populated by the pipeline) and folds the slots into a bullet
    instead of letting the LLM guess availability."""

# drafting/expand.py
def expand_outline_to_full_draft(email_id: str) -> str:
    """Stub for Phase 5; real implementation is a later phase. Takes a
    reply_outline and expands it into full prose the user can send as-is
    or edit further."""
```

---

## Context graph (Track A) → produces `chunk`/`entity`/`mention`/`relation` rows

> Added at Checkpoint 0 (`PHASES-COMPLEX.md`), append-only: a new layer
> alongside `ProcessedEmail`, not a replacement for it. Types referenced
> below (`Chunk`, `Entity`, `Mention`, `Relation`, `EntityKind`, `ChunkKind`)
> are defined in `models/schema.py`.

```python
# context/chunk.py
def chunk_email(
    raw: RawEmail,
    *,
    target_chars: int = 800,
    overlap: int = 100,
) -> list[Chunk]:
    """Strips quoted reply history and signatures BEFORE chunking — load-
    bearing, not tidiness: unstripped quote blocks make every email in a
    thread look near-identical, poisoning embeddings and misattributing
    entities. Stripped text is not discarded — emitted as kind=QUOTED/
    SIGNATURE chunks so nothing is silently lost, just excluded from
    embedding/extraction downstream. Remaining kind=BODY text is chunked on
    paragraph boundaries to ~target_chars with overlap; never splits
    mid-sentence."""

# context/extract.py
def extract_entities(
    raw: RawEmail,
    chunks: Sequence[Chunk],
) -> list[Mention]:
    """Deterministic first, LLM second (this repo's standing philosophy —
    see classification/rules.py before classification/llm_fallback.py).
    Pass 1 (free, exact): PERSON/ORG from headers, CASE/DOCUMENT ids by
    regex — source=HEADER/REGEX, confidence 1.0. Pass 2 (one LLM call,
    stage "extract"): PROJECT/DELIVERABLE/TOPIC only, plus which pass-1 ids
    are this email's actual subject vs. an incidental mention — given only
    kind=BODY chunks, never QUOTED/SIGNATURE. Schema declares `reason`
    before the answer arrays; maxLength on every string."""

# context/resolve.py
def resolve(mentions: list[Mention], existing: "EntityIndex") -> "ResolveResult":
    """Deterministic ladder, first match wins: (1) exact normalized_key
    within the same EntityKind, (2) entity_alias match, (3) same-kind
    embedding cosine >= 0.86 against entity_vec, (4) else create a new
    entity. PERSON entities key on the bare email address, never display
    name. Pure and unit-testable — no model, no DB; embeddings are passed
    in, not computed inside, so the 0.86 threshold can be tuned against
    hand-built vectors in a test."""

# context/store.py
def upsert_chunks(chunks: list[Chunk], *, db_path=None) -> None: ...
def upsert_vectors(pairs: list[tuple[str, bytes]], *, db_path=None) -> None: ...
def upsert_entities(entities: list[Entity], *, db_path=None) -> None: ...
def upsert_mentions(mentions: list[Mention], *, db_path=None) -> None: ...
def entities_for_email(email_id: str, *, db_path=None) -> list[Entity]: ...
def emails_for_entity(entity_id: str, *, db_path=None) -> list[str]: ...
def neighbors(entity_id: str, *, hops: int = 1, db_path=None) -> list[Entity]: ...
def load_all_vectors(*, db_path=None) -> tuple[list[str], "np.ndarray"]:
    """One contiguous matrix, not a list of arrays — this is Track B's hot
    path for the vector search channel."""

# context/consolidate.py
def consolidate(db_path=None) -> "ConsolidateStats":
    """Corpus-wide: resolves pending mentions via context.resolve, derives
    relation edges (participant_in by co-occurrence, belongs_to when a
    project co-occurs with a case across >=2 emails, symmetric mentions
    edges otherwise), then marks affected node_brief rows dirty by
    recomputing evidence_hash. Must run after the WHOLE corpus's
    chunk/embed/extract pass, before ANY email's reasoning pass — see
    pipeline/orchestrate.py's process_context_one docstring."""
```

## Embeddings (Track A) → local vectors for entity resolution and retrieval

```python
# llm/embeddings.py
def embed_texts(texts: list[str], *, model: str = "nomic-embed-text") -> list[bytes]:
    """POSTs to ollama's /api/embed, batched. Vectors are normalized ON
    WRITE and stored as float32 little-endian BLOBs, so cosine at query
    time is a plain dot product — required for Track B's search path to
    stay fast."""

def cosine(a: bytes, b: bytes) -> float: ...
def cosine_matrix(query: bytes, matrix: "np.ndarray") -> "np.ndarray": ...
def to_blob(vec) -> bytes: ...
def from_blob(blob: bytes) -> "np.ndarray": ...

def check() -> str:
    """Distinguishes 'ollama server is not running' from 'nomic-embed-text
    is not pulled' — model llm/ollama.py:201's check()."""
```

## Retrieval (Track B) → produces `ContextPack`/`Brief`

```python
# retrieval/search.py
@dataclass
class ScoredChunk:
    chunk_id: str
    email_id: str
    text: str
    score: float
    channel: str

def search(
    query: str,
    *,
    k: int = 12,
    anchor_email_id: str | None = None,
    filters: dict | None = None,
    db_path=None,
) -> list[ScoredChunk]:
    """Three independent channels — FTS5 bm25() over chunk_fts, cached-
    matrix vector cosine, and a graph walk (entities_for_email -> neighbors
    -> emails_for_entity, decayed per hop) — fused with Reciprocal Rank
    Fusion (sum of 1/(60+rank) per channel), never a weighted sum of raw
    scores. The graph channel is what produces cross-thread correlation."""

# retrieval/pack.py
def build_pack(
    *,
    anchor_email_id: str | None = None,
    query: str | None = None,
    budget_chars: int = 6000,
    db_path=None,
) -> ContextPack:
    """The ONLY context-assembly function in the codebase — outline
    generation, context-aware summarization, and agent/tools.py's
    search_context tool all call this, never build context strings by
    hand. Fixed priority order until budget_chars is spent: anchor's own
    summary/subject, thread brief, case/project briefs, their open_items,
    then top-k foreign chunks from search(). Every foreign ContextSection's
    label carries provenance ("From <sender>, <date>, re: <subject>").
    Deduplicates by email_id; never includes the anchor's own chunks as
    foreign."""

# retrieval/briefs.py
def rebuild_dirty(db_path=None, *, limit: int | None = None) -> int:
    """One LLM call (stage 'brief') per node whose evidence_hash changed.
    Skips nodes with fewer than 2 emails, and skips entirely when
    evidence_hash is unchanged — the whole point of storing it. Brief
    content is a state document (what's happened / who's involved / what's
    open / what was decided), not a per-email summary list."""

def get_brief(node_type: str, node_id: str, db_path=None) -> Brief | None: ...
```

## Agent (Track C) → the in-app assistant

```python
# agent/tools.py
TOOL_SPECS: list[dict]   # Anthropic tool-definition format

def dispatch(name: str, args: dict, *, db_path=None) -> dict:
    """Nine tools, each reusing an existing implementation rather than
    reimplementing it — see agent/tools.py's own docstring for the mapping.
    Every result is JSON-serializable AND bounded (capped lists, truncated
    text) — an unbounded get_email on a 5000-char body blows the loop's
    context by turn three."""

# agent/loop.py
def run(messages: list[dict], *, max_turns: int = 8, db_path=None) -> "Iterator[Event]":
    """Standard Anthropic tool-use loop via llm.client.get_client("agent").
    Raises immediately, before the first API call, if the resolved provider
    is ollama — llm/ollama.py has no tool-calling support and would
    otherwise silently ignore `tools=` and return a confident, tool-free
    answer. Yields text_delta/tool_start/tool_end/done events so the
    transport layer can stream them. Never claims to have sent anything;
    grounds answers in tool results and cites email_ids."""
```

---

## Notes on gating (applies to Drafting)

`generate_reply_outline` must check `read_status` and `is_no_reply` with a
plain `if` before touching any LLM client — not via a prompt instruction
telling the model to skip ineligible emails. This is a hard correctness
requirement (see `CONTEXT.md`'s design rationale) and Phase 5's tests must
assert it directly (e.g. by asserting the LLM client is never invoked for
unread/no-reply fixtures, not just asserting the output is null).
