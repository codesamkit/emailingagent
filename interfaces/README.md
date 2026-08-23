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

## Action items (Track B) → produces `action_items`

> Added post-Phase 8 via the append-only schema process: new
> `ProcessedEmail.action_items` field and new function, no existing
> signature changed.

```python
# summarization/action_items.py
def extract_action_items(email: RawEmail) -> list[str]:
    """Concrete tasks/asks/deadlines the email states or clearly implies for
    the recipient. Unlike summarize(), which explicitly omits inferred
    action items, and unlike reply_outline (only set for read, non-no-reply
    emails), this runs for EVERY email regardless of read/no-reply status —
    a no-reply notification (e.g. an invoice) can still carry a real
    deadline. Single LLM call, routable independently as stage
    "action_items". Empty list if the email asks nothing of the recipient."""
```

## To-do list (application layer) → produces `todo_item` rows

> Added post-Phase 8. A derived, user-checkable list layered on top of
> `ProcessedEmail` — not a new track output, and not part of the frozen
> `ProcessedEmail`/`RawEmail` contract. Types referenced below (`TodoItem`,
> `TodoKind`, `TodoStatus`) are defined in `models/schema.py`.

```python
# pipeline/todo.py
def sync(emails: list[ProcessedEmail], *, db_path=None) -> int:
    """Inserts any new open todo rows implied by `emails` — one per string
    in action_items, plus one "needs_reply" row per email that is not
    no-reply and hasn't been replied to yet. todo_id is a stable hash of
    (email_id, kind, text), so re-deriving the same item on a later
    pipeline run recognizes it instead of inserting a duplicate — the
    mechanism that lets a user's completion survive the next refresh.
    needs_reply is also the one kind this function closes on its own: it's
    fully determined by the email's current state, so a row whose condition
    no longer holds is auto-resolved rather than left open forever. Called
    once per batch from pipeline/refresh.py, after persistence."""

def list_open(*, db_path=None) -> list[dict]:
    """Open todo rows joined with their email's subject/sender/importance,
    most important first. Consumed by GET /api/todos."""

def complete(todo_id: str, *, db_path=None) -> bool:
    """Marks one open row done. Returns False if it's unknown or already
    done. Consumed by POST /api/todos/{todo_id}/complete — human-triggered
    only, like every other mutating endpoint in api/main.py."""
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
      - is_no_reply == True            -> (None, ReplyOutlineStatus.NOT_APPLICABLE)
      - is_no_reply is None            -> (None, ReplyOutlineStatus.NONE) — unclassified
      - else                           -> generate outline bullets,
                                           (bullets, ReplyOutlineStatus.SUGGESTED)
    Read status is not part of this gate — drafting runs on arrival, not on open.
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

# Context graph + retrieval (post-Phase 8)

> Added via the append-only process, like Categorization above: new dataclasses
> in `models/schema.py`, eleven new tables in `models/db.py`, no existing
> signature changed. See `PHASES-COMPLEX.md` for the design rationale and the
> three-way ownership map.

Track ownership below the shared contract:

| | Owns | Also edits |
|---|---|---|
| A | `context/` | `llm/embeddings.py` |
| B | `retrieval/` | `drafting/outline.py`, `summarization/summarize.py`, `scoring/signals.py` |
| C | `agent/`, `extension/` | `api/main.py` |

Types referenced here — `Chunk`, `ChunkKind`, `Entity`, `EntityKind`,
`Mention`, `MentionSource`, `Relation`, `RelationKind`, `Brief`,
`BriefNodeType`, `ContextSection`, `ContextPack` — are all in
`models/schema.py`.

**Two contract details worth reading before you build against them:**

1. `node_brief` is keyed on **`(node_type, node_id)`**, not `node_id` alone.
   `node_id` is a Gmail `thread_id` for THREAD briefs and an `entity_id` for
   the other three, so upserts need `ON CONFLICT (node_type, node_id)`.
2. The context pass is a **separate pass**, not three more entries in
   `pipeline.orchestrate.STAGES`. `CONTEXT_STAGES` has its own entry point,
   `Pipeline.run_context()` / `run_context_one()`, because every email's
   context must be extracted and consolidated before *any* email's outline is
   generated. Stage names are disjoint across the two lists; `ALL_STAGE_NAMES`
   is the union, for CLI validation only.
3. **"Dirty" means `node_brief.headline IS NULL`.** There is no flag column.
   `context.consolidate` writes each node's CURRENT `evidence_email_ids` and
   `evidence_hash`, and clears `headline` / `body_md` / `open_items` when the
   hash moved; a node whose hash is unchanged is not touched at all, which is
   what makes a no-op run cost zero brief calls. Track B's work queue is
   `context.store.dirty_briefs()` — do not re-derive the query, and do not
   recompute the hash: `context.consolidate.evidence_hash()` is the one
   definition, and it includes each email's `processed_at`, so a re-summarized
   email correctly counts as new evidence. After generating, write the brief
   back with `context.store.upsert_briefs()`, carrying the same
   `evidence_hash` the dirty row already holds.
4. Populating the graph before the integration step: `python -m context.cli
   build` runs the context pass plus `consolidate` over stored mail. It calls
   the same `Pipeline.run_context_one` the integrated path will, so it is not a
   second implementation — it exists because the graph has to be inspectable
   before `pipeline/refresh.py` is rewired (INT1).

## Context (Track A) → produces chunks, entities, mentions, relations

```python
# context/chunk.py
def chunk_email(
    raw: RawEmail,
    *,
    target_chars: int = 800,
    overlap: int = 100,
) -> list[Chunk]:
    """Split one email into embeddable spans, quoted reply history and
    signatures stripped out of the body FIRST and re-emitted as separate
    Chunks with kind=QUOTED / kind=SIGNATURE — stored, never silently
    dropped, but excluded from embedding and extraction downstream.
    BODY chunks break on paragraph boundaries to roughly target_chars with
    `overlap` characters of carry-over, and never mid-sentence. `ord` is the
    position within the email across all kinds."""

# context/normalize.py — pure string work, shared by extract and resolve
def normalize_name(text: str, kind: EntityKind | None = None) -> str:
    """The identity key for a surface form: lowercased, punctuation dropped,
    leading article and generic noun removed ("the Atlas project" -> "atlas"),
    trailing plural folded for the common-noun kinds. Conservative on purpose —
    over-merging is this layer's primary risk."""
def normalize_id(text: str) -> str:
    """The identity key for a machine id: "cs-40350" / "CS 40350" -> "CS40350".
    Never singularized, never word-dropped."""
def normalize_address(value: str) -> str:
    """A bare lowercased address, via scoring.signals._addr_only."""
def provisional_id(kind: EntityKind, normalized_key: str) -> str:
    """"<kind>:<key>" — what a Mention.entity_id holds before resolution.
    `Mention` has no `kind` field, and extraction runs before any entity
    exists, so the kind rides in the string and `resolve` rewrites it to a real
    entity_id. A real entity_id contains no ":", so `parse_provisional`
    returning None doubles as "already resolved"."""
def parse_provisional(entity_id: str) -> tuple[EntityKind, str] | None: ...

# context/embed.py
def embed_chunks(chunks: Sequence[Chunk]) -> list[tuple[str, bytes]]:
    """(chunk_id, vector blob) for the BODY chunks only. Thin adapter over
    llm.embeddings.embed_texts so the pipeline injects one callable per stage;
    non-BODY chunks are dropped rather than embedded."""

# context/extract.py
def extract_entities(raw: RawEmail, chunks: Sequence[Chunk]) -> list[Mention]:
    """Deterministic first, LLM second (the same posture as
    classification/rules.py before classification/llm_fallback.py).
    Pass 1, free and exact: PERSON/ORG from From/To/Cc/Reply-To, ORG from the
    sender domain excluding free-mail providers; CASE/DOCUMENT ids by regex
    over subject and body. Emitted with source=HEADER or REGEX, confidence 1.0.
    Pass 2, one call on the "extract" stage: PROJECT / DELIVERABLE / TOPIC,
    which regex cannot get, plus a judgment on which pass-1 ids this email is
    actually *about* versus incidentally mentions. source=LLM.
    `chunks` should be BODY chunks only; quoted and signature text is never
    shown to the model and never yields a mention."""

# context/resolve.py
class EntityIndex:
    """Read model over already-known entities. Pure — no DB, no model."""
    by_key: dict[tuple[EntityKind, str], Entity]
    by_alias: dict[tuple[EntityKind, str], Entity]
    vectors: dict[str, bytes]          # entity_id -> vector blob

@dataclass
class ResolveResult:
    mentions: list[Mention]            # entity_id now points at a real entity
    entities: list[Entity]             # created or updated, ready to upsert
    aliases: list[tuple[str, str]]     # (entity_id, alias)
    created: int
    merged: int

def resolve(
    mentions: Sequence[Mention],
    existing: EntityIndex,
    *,
    embeddings: Mapping[str, bytes] | None = None,
    threshold: float = 0.86,
) -> ResolveResult:
    """Deterministic ladder, first match wins: (1) exact normalized_key within
    the same EntityKind, (2) alias match, (3) same-kind cosine >= threshold
    against entity vectors, (4) create.
    Pure by construction — no DB and no model call. Embeddings are passed in
    rather than computed here so the threshold can be tuned against hand-built
    vectors in a unit test; it is a guess, and it is the first number that will
    move after real output is seen. PERSON entities key on the bare email
    address, never a display name."""

# context/store.py — thin persistence, every connection via models.db
def upsert_chunks(chunks, *, db_path=None) -> int: ...
def upsert_vectors(pairs, *, db_path=None) -> int: ...          # (id, blob)
def upsert_entities(entities, *, db_path=None) -> int: ...
def upsert_aliases(pairs, *, db_path=None) -> int: ...
def upsert_mentions(mentions, *, db_path=None) -> int: ...
def upsert_relations(relations, *, db_path=None) -> int: ...
def entities_for_email(email_id, *, db_path=None) -> list[Entity]: ...
def emails_for_entity(entity_id, *, db_path=None) -> list[str]: ...
def neighbors(entity_id, *, hops=1, db_path=None) -> list[tuple[Entity, float, int]]:
    """(entity, accumulated edge weight, hop distance), nearest hop first."""
def chunks_for_email(email_id, *, kind=None, db_path=None) -> list[Chunk]: ...
def load_all_vectors(*, db_path=None) -> tuple[list[str], "np.ndarray"]:
    """(chunk_ids, matrix). ONE contiguous (n, dim) float32 matrix, not a list
    of arrays — this is the vector channel's hot path. Rows are L2-normalized
    on write, so similarity is `matrix @ query`, no renormalization."""
def context_coverage(*, db_path=None) -> dict[str, set[str]]:
    """{context stage: email_ids that already have rows}, for
    pipeline.incremental.context_plan."""
def load_entity_index(*, db_path=None) -> EntityIndex: ...
def get_brief(node_type, node_id, *, db_path=None) -> Brief | None: ...
def upsert_briefs(briefs, *, db_path=None) -> int: ...
def dirty_briefs(*, db_path=None) -> list[Brief]:
    """Briefs awaiting generation (headline IS NULL), largest evidence set
    first. Track B's work queue — see contract detail 3 above."""
def counts(*, db_path=None) -> dict[str, int]: ...
def entity_counts_by_kind(*, db_path=None) -> dict[str, int]: ...
def email_counts_for_entities(*, db_path=None) -> dict[str, int]: ...

# context/consolidate.py
@dataclass
class ConsolidateStats:
    mentions_resolved: int
    entities_created: int
    entities_merged: int
    relations_written: int
    briefs_dirtied: int

def evidence_hash(pairs: Sequence[tuple[str, str | None]]) -> str:
    """Hash of (email_id, processed_at) pairs — a node's evidence fingerprint.
    The ONE definition; Track B reads the stored value rather than recomputing."""

def consolidate(db_path=None, *, threshold=0.86, embed=None) -> ConsolidateStats:
    """Corpus-wide, run once after the context pass and before the reasoning
    pass. Resolves pending mentions, derives relation edges (PERSON
    --participant_in--> CASE|PROJECT by co-occurrence weighted by mention
    count; CASE --belongs_to--> PROJECT only when they co-occur across 2+
    emails, since one co-occurrence is coincidence; symmetric `mentions` edges
    otherwise), recomputes entity salience, and marks node_brief rows dirty by
    writing a fresh evidence_hash. Track B's brief rebuild reads that hash, so
    a stale one means briefs never refresh."""
```

```python
# llm/embeddings.py (Track A) — local, free, no hosted call
def embed_texts(texts: Sequence[str], *, model: str = "nomic-embed-text") -> list[bytes]:
    """Batched POST to ollama /api/embed. Returns one float32 little-endian
    blob per input, in input order. Vectors are L2-NORMALIZED ON WRITE, which
    is what makes cosine a plain dot product on the read path."""
def cosine(a: bytes, b: bytes) -> float: ...
def cosine_matrix(query: bytes, matrix: "np.ndarray") -> "np.ndarray": ...
def to_blob(vec) -> bytes: ...
def from_blob(blob: bytes) -> "np.ndarray": ...
def check() -> str:
    """One human-readable line. Distinguishes "ollama is not running" from
    "nomic-embed-text is not pulled" — different fixes, and both otherwise
    surface as an opaque failure."""
```

## Retrieval (Track B) → produces `ContextPack`

```python
# retrieval/search.py
@dataclass
class ScoredChunk:
    chunk_id: str
    email_id: str
    text: str
    score: float
    channel: str                       # "bm25" | "vector" | "graph"

def search(query, *, k=12, anchor_email_id=None, filters=None, db_path=None) -> list[ScoredChunk]:
    """Three independent channels, each ranked separately, then fused by
    Reciprocal Rank Fusion (`sum over channels of 1/(60 + rank)`) — NOT a
    weighted sum of raw scores, because BM25 scores, cosines, and graph weights
    are not commensurable and one channel would silently dominate.
      bm25    FTS5 MATCH over chunk_fts, kind='body' only. Nails exact ids.
      vector  query embedding · the pre-normalized chunk matrix.
      graph   entities_for_email -> neighbors -> emails_for_entity -> chunks,
              scored by salience x edge weight, decayed per hop. This is the
              channel that produces cross-thread correlation."""

# retrieval/pack.py
def build_pack(*, anchor_email_id=None, query=None, budget_chars=6000, db_path=None) -> ContextPack:
    """The ONLY context-assembly function in the codebase. Outline generation,
    context-aware summarization, and the agent's search tool all call this;
    nobody builds a context string by hand.
    Fixed priority order until the budget is spent: (1) anchor's own subject +
    summary, (2) the thread brief if the thread has more than one message,
    (3) case and project briefs for the anchor's entities, (4) their
    open_items, (5) top-k foreign chunks from search().
    Every foreign section's label carries provenance
    ("From <sender>, <date>, re: <subject>"). Deduplicated by email_id, and the
    anchor's own chunks are never included as foreign."""

# retrieval/briefs.py
def rebuild_dirty(db_path=None, *, limit=None) -> int:
    """One "brief"-stage call per node in `context.store.dirty_briefs()`. Two
    gates decide the cost: only nodes with 2+ emails (160 single-email briefs
    say nothing the summary didn't), and an unchanged hash never reaches the
    queue in the first place — consolidate leaves those rows untouched.
    Write results back with `context.store.upsert_briefs`, preserving the
    `evidence_hash` already on the row."""
def get_brief(node_type, node_id, db_path=None) -> Brief | None:
    """Thin pass-through to `context.store.get_brief` — one read path."""
```

## Agent (Track C) → the in-app assistant

```python
# agent/tools.py
TOOL_SPECS: list[dict]                 # Anthropic tool-definition format
def dispatch(name: str, args: dict, *, db_path=None) -> dict:
    """Nine tools, each REUSING an existing implementation rather than a second
    one: search_context -> retrieval.pack.build_pack; get_email ->
    pipeline.persist.get + ingestion.store.get; get_thread_brief /
    get_entity_brief -> retrieval.briefs.get_brief; list_entities ->
    context.store; find_open_items -> node_brief.open_items; list_queue ->
    api.filters; draft_reply -> drafting.expand; summarize_selection ->
    retrieval.pack + one call.
    Every result is JSON-serializable AND bounded — lists capped, long text
    truncated — because an unbounded get_email blows the loop's context by
    turn three."""

# agent/loop.py
def run(messages, *, max_turns=8, db_path=None) -> Iterator[dict]:
    """Anthropic tool-use loop on the "agent" stage, yielding
    text_delta / tool_start / tool_end / done events so the transport can
    stream. Raises before the first call if the resolved provider is ollama:
    llm/ollama.py has no `tools` support and would ignore the parameter,
    returning a confident answer having called nothing."""

# agent/conversation.py — agent_conversation + agent_message tables
def create(title=None) -> str: ...
def append(conversation_id, role, content) -> None: ...
def history(conversation_id, limit=None) -> list[dict]: ...
def recent(limit=20) -> list[dict]: ...
```

---

## Notes on gating (applies to Drafting)

`generate_reply_outline` must check `is_no_reply` with a plain `if` before
touching any LLM client — not via a prompt instruction telling the model to
skip ineligible emails. This is a hard correctness requirement (see
`CONTEXT.md`'s design rationale) and Phase 5's tests must assert it directly
(e.g. by asserting the LLM client is never invoked for no-reply/unclassified
fixtures, not just asserting the output is null). Read status is
deliberately NOT part of this gate — drafting runs as soon as an email
arrives and clears classification, not when the user opens it.

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
