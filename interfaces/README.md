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

## Notes on gating (applies to Drafting)

`generate_reply_outline` must check `read_status` and `is_no_reply` with a
plain `if` before touching any LLM client — not via a prompt instruction
telling the model to skip ineligible emails. This is a hard correctness
requirement (see `CONTEXT.md`'s design rationale) and Phase 5's tests must
assert it directly (e.g. by asserting the LLM client is never invoked for
unread/no-reply fixtures, not just asserting the output is null).

---

# Context graph — frozen after Checkpoint 0 (PHASES-COMPLEX.md)

The next architectural layer on top of Phases 0–8. New types
(`Chunk`, `Entity`, `Mention`, `Relation`, `Brief`, `ContextSection`,
`ContextPack`, and the `EntityKind`/`ChunkKind`/`MentionSource` enums) live in
`models/schema.py`; their tables live in `models/db.py`. Same rule as above —
document, don't implement; changes require flagging Track A/B/C first.

## Context substrate (Track A) → produces `Chunk`/`Entity`/`Mention`/`Relation`

```python
# context/chunk.py
def chunk_email(
    raw: RawEmail, *, target_chars: int = 800, overlap: int = 100
) -> list[Chunk]:
    """Strips quoted reply history and signatures BEFORE chunking (emitted as
    kind=QUOTED/kind=SIGNATURE chunks, not discarded), then splits the
    remaining kind=BODY text on paragraph boundaries, never mid-sentence."""

# context/extract.py
def extract_entities(raw: RawEmail, chunks: list[Chunk]) -> list[Mention]:
    """Deterministic pass (headers + regex, source=HEADER/REGEX, confidence
    1.0) first, then one LLM call (source=LLM) for PROJECT/DELIVERABLE/TOPIC
    plus which pass-1 IDs are the email's actual subject vs. incidental."""

# context/resolve.py
def resolve(mentions: list[Mention], existing: "EntityIndex") -> "ResolveResult":
    """Pure, no model/DB calls — embeddings passed in, not computed here.
    Ladder: exact normalized_key -> entity_alias -> same-kind cosine >= 0.86
    -> new entity."""

# context/store.py
def upsert_chunks(chunks: list[Chunk], *, db_path=None) -> None: ...
def upsert_vectors(pairs, *, db_path=None) -> None: ...
def upsert_entities(entities: list[Entity], *, db_path=None) -> None: ...
def upsert_mentions(mentions: list[Mention], *, db_path=None) -> None: ...
def entities_for_email(email_id: str, *, db_path=None) -> list[Entity]: ...
def emails_for_entity(entity_id: str, *, db_path=None) -> list[str]: ...
def neighbors(entity_id: str, *, hops: int = 1, db_path=None) -> list[Entity]: ...
def load_all_vectors(*, db_path=None) -> tuple[list[str], "np.ndarray"]:
    """One contiguous matrix, not a list of arrays — retrieval's hot path."""

# context/consolidate.py
def consolidate(db_path=None) -> "ConsolidateStats":
    """Corpus-wide: resolve pending mentions, derive relation edges, then
    mark affected node_brief rows dirty (evidence_hash = hash of sorted
    email_ids + their processed_at values)."""
```

> **Retrieval's `retrieval/search.py` currently implements a small private
> seam duplicating `entities_for_email`/`neighbors`/`emails_for_entity`/
> `load_all_vectors` via direct SQL**, since this track hasn't landed in this
> repo yet. Swap to these real functions once `context/store.py` ships —
> same signatures, so the swap is a one-line import change.

## Local embeddings (Track A) → produces `chunk_vec`/`entity_vec`

```python
# llm/embeddings.py
def embed_texts(texts: list[str], *, model: str = "nomic-embed-text") -> list[bytes]:
    """Batched POST to ollama's /api/embed. Vectors NORMALIZED ON WRITE so
    cosine at query time is a plain dot product."""

def cosine(a: bytes, b: bytes) -> float: ...
def cosine_matrix(query: bytes, matrix: "np.ndarray") -> "np.ndarray": ...
def to_blob(vec) -> bytes: ...
def from_blob(blob: bytes) -> "np.ndarray": ...
def check() -> str:
    """Distinguishes 'ollama not running' from 'nomic-embed-text not
    pulled' as two different messages."""
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
    query: str | None = None,
    *,
    k: int = 12,
    anchor_email_id: str | None = None,
    filters: dict | None = None,
    db_path=None,
) -> list[ScoredChunk]:
    """Three channels (BM25 over chunk_fts, vector via a module-level cached
    matrix, graph-walk via context.store) fused with Reciprocal Rank Fusion —
    not a weighted sum of raw scores, which aren't commensurable."""

# retrieval/pack.py
def build_pack(
    *,
    anchor_email_id: str | None = None,
    query: str | None = None,
    budget_chars: int = 6000,
    db_path=None,
) -> ContextPack:
    """The ONLY context-assembly function in the codebase — outline.py,
    summarize.py, and agent/loop.py all call this, never build context
    strings by hand. Fixed priority order until budget is spent: anchor's own
    summary/subject -> thread brief -> case/project briefs -> their
    open_items -> top-k foreign chunks. Every foreign ContextSection is
    labeled "From <sender>, <date>, re: <subject>". Anchor's own chunks never
    appear as foreign."""

# retrieval/briefs.py
def rebuild_dirty(db_path=None, *, limit: int | None = None) -> int:
    """One LLM call per dirty node with >=2 emails (skip fewer — one-email
    briefs restate the summary); skips entirely when evidence_hash is
    unchanged."""

def get_brief(node_type: str, node_id: str, db_path=None) -> Brief | None: ...
```

## The agent (Track C) → produces `agent_conversation`/`agent_message`

```python
# agent/tools.py
TOOL_SPECS: list[dict]   # Anthropic tool-definition format

def dispatch(name: str, args: dict, *, db_path=None) -> dict:
    """Nine tools (search_context, get_email, get_thread_brief,
    get_entity_brief, list_entities, find_open_items, list_queue,
    draft_reply, summarize_selection), each reusing an existing
    implementation — never a second filtering/drafting path. Every result is
    JSON-serializable AND bounded (capped lists, truncated text)."""

# agent/loop.py
def run(messages: list[dict], *, max_turns: int = 8, db_path=None) -> "Iterator[Event]":
    """Standard Anthropic tool-use loop via llm.client.get_client("agent").
    Raises immediately (before the first API call) if the resolved provider
    is ollama — llm/ollama.py has no tool-calling support and would
    otherwise silently ignore `tools` and return a confident, ungrounded
    answer."""
```

## Notes on the context-graph layer

Same hard-gate rule as Drafting above: `drafting/outline.py:is_eligible` is
never touched by this layer. `context: ContextPack | None = None` is an
additive, defaulted parameter on `generate_reply_outline` and `summarize` —
omitting it preserves every existing caller's behavior exactly.

`llm/ollama.py` has no tool-calling support — `agent/loop.py` is the only
consumer of `"tools"`, and it must fail loudly on an ollama-routed `"agent"`
stage rather than silently dropping the parameter.
