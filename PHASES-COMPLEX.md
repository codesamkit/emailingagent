# Valence — Context Graph + In-App Agent

A parallel build plan for three people. Read §1 and §2 before touching any file.

This supersedes nothing in `PHASES.md` — phases 0–8 there are built and shipped. This is the next architectural layer on top of them.

## 0. Prerequisites (everyone, before Checkpoint 0)

```bash
ollama pull nomic-embed-text        # 274 MB, 768-dim. Required — not currently pulled.
ollama list                         # confirm nomic-embed-text AND a chat model are present
pip install -r requirements.txt     # numpy is newly added
```

Environment:

```bash
export LLM_PROVIDER=anthropic
export LLM_MODEL_EXTRACT=claude-sonnet-5     # span extraction — opus is overkill
export LLM_MODEL_BRIEF=claude-sonnet-5
export LLM_MODEL_AGENT=claude-opus-5         # the agent needs the reasoning
export EMAIL_AGENT_DB=./ingestion/data/emails.db
```

**Do not route the agent to ollama.** `llm/ollama.py` has no tool-calling support. Person C's loop must raise a clear error rather than degrade silently.

Known environment facts, already verified — don't re-litigate them:

- Python 3.9.6. `sqlite3.Connection.enable_load_extension` is **unavailable**, so `sqlite-vec` / `sqlite-vss` cannot load. Vectors are float32 BLOBs + numpy.
- FTS5 **is** compiled in and works.
- SQLite 3.51.0.
- The live DB currently has 2 rows. The ~160 test emails still need ingesting.

---

## 1. Why this design

### The problem we're fixing

Every LLM call in this repo currently sees **exactly one email and nothing else**:

- `summarization/summarize.py:52` — identity header + body.
- `drafting/outline.py:96` — identity header + this email's own summary + its own body.
- `scoring/score.py:180` — sender/subject/truncated body + its own rule signals.

`thread_id` is stored and indexed on both tables but **never joined on** to gather sibling messages; its only consumer is `pipeline/staleness.py`. There are no embeddings, no vector store, no FTS5 index, no chunk or entity table anywhere. `api/filters.py:58` "search" is a Python substring scan over every row loaded into memory.

Outlines are therefore generic *by construction*. We ask the model to write a specific reply while showing it a single message stripped of every fact that would make it specific.

### The substrate choice

We evaluated vector DB / tree / embeddings tree / working tree. **None alone. We're building an entity-centric graph in SQLite, with embeddings and FTS5 as two retrieval channels into it, plus hierarchical rollup briefs as working memory.**

**Not a pure vector database.** What we need is a *join on identity, not a join on similarity*. Two emails belong to the same case even when they share almost no vocabulary — one says "the Henderson escalation," another says "ticket 4471," a third is a forwarded invoice with a PO number. Cosine similarity scores those three as unrelated. An exact key — case ID, participant set, project name — links them with certainty. At 160 emails there also isn't enough text for semantic similarity to be the differentiator.

**Not a pure tree.** Email context isn't a tree. One email touches several cases; one person spans many projects; a thread forks into two workstreams. Forcing a tree means picking one parent and discarding the other edges — which is exactly the correlation we're trying to capture. It's a DAG.

**Embeddings still earn their place,** for two specific jobs: entity resolution (deciding "Henderson escalation" and "Henderson issue" are one node) and fallback recall when no shared identifier exists. They are a channel into the graph, not the architecture of it.

**"Working tree" = the `node_brief` table.** A cached, LLM-written state document per thread / case / project / person, regenerated only when its evidence set changes. Roll-ups *are* tree-shaped (email → thread → case → project) even though the edges underneath are a graph — so we get the tree's benefits without forcing tree topology on the links.

**Sizing.** ~160 emails ≈ ~1,500 chunks × 768 dims float32 ≈ 4.6 MB. Brute-force cosine ≈ 5 ms in numpy. No ANN index, no vector DB. Anything more is unjustified at this scale.

### Right fix vs. band-aid

The band-aid would be stuffing the whole thread into the outline prompt — ~20 lines, visibly better replies on multi-message threads, and **not what was asked for**: it does nothing for correlation across *separate* threads, which is the entire premise. The retrieval substrate is the real fix and it subsumes the band-aid, since thread history is just the cheapest edge in the graph.

### Architecture

```
raw_email ──chunk──> chunk ──embed──> chunk_vec        (local nomic-embed-text)
                       ├──fts───────> chunk_fts        (FTS5, free)
                       └─extract────> mention ──resolve──> entity ──> relation
                                                              │
                                              consolidate ────┴──> node_brief
                                                                   (thread/case/project/person)
                                          ┌───────────────────────────┘
                       retrieval.pack ────┤  BM25 + vector + graph-walk, RRF-fused
                                          └──> ContextPack (char-budgeted, cited)
                                                    │
                        ┌───────────────────────────┼──────────────────┐
                   outline.py              summarize.py           agent/loop.py
                (context-aware)         (context-aware)        (Claude tool loop)
```

### Two-pass refresh — ordering matters

If extraction runs in the same per-email pass as outline generation, email #1's outline can't see email #160's case; the graph isn't built yet. So `pipeline/refresh.py::process_incremental` splits into:

1. **Context pass** — `chunk` → `embed` → `extract`, per email.
2. **Consolidate** — corpus-wide: resolve entities, rebuild dirty briefs. Same shape as the existing `apply_feedback_priors` / `apply_score_spread` post-passes.
3. **Reasoning pass** — the existing 8 stages, now with a populated graph to retrieve from.
4. **Existing post-passes** — unchanged.

### One retrieval function, three consumers

`retrieval/pack.py::build_pack(...) -> ContextPack` is the **single** entry point. Outline generation, context-aware summarization, and the agent's `search_context` tool all call it. Nobody hand-rolls context assembly (CLAUDE.md's DRY rule). Three channels fused by Reciprocal Rank Fusion:

1. **BM25** over `chunk_fts` — exact IDs, names, numbers. Free and precise.
2. **Vector cosine** over `chunk_vec` — paraphrase recall.
3. **Graph walk** — entities of the anchor email, 1–2 hops, their emails and briefs. *This is the channel that produces the cross-thread correlation.*

Packed under a char budget in fixed priority order — anchor email > thread brief > case/project brief > open items > top-k foreign chunks — each foreign chunk carrying its `email_id`, sender, and date so the model cites rather than blurs.

---

## 2. Rules of engagement

Same convention as `CONTEXT.md`: one frozen contract, then exclusive folders.

1. Checkpoint 0 is done by all three **together**. Do not parallelize it. It is the only shared surface.
2. After that, **nobody edits a file outside their track.** If your change needs a file another track owns, stop and coordinate — that's a signal the split is wrong, not a merge to push through.
3. Everyone builds against fixtures, so no track ever waits on another. Only *merges* are ordered.
4. Additive-optional parameters only when touching existing functions. All 463 existing tests must keep passing untouched.
5. Reuse, don't re-create: `models.db.connect` / `prepare` for every connection, `llm.client.get_client(stage)` for every model call, `llm.prompting.email_identity_block` for every From/To header block.
6. Two load-bearing LLM conventions in this repo — follow them:
   - **Declare the `reason` field before the answer field** in every JSON schema. Constrained decoding emits fields in declaration order, so reason-first *informs* the answer instead of rationalizing it (documented at `scoring/score.py:92`).
   - `maxLength` bounds on every string field are structural anti-repetition guards, not cosmetics.
7. **Hard rules live in code, not prompts.** The outline gate (`drafting/outline.py:71`), the no-reply level cap (`scoring/score.py:226`), the feedback priors — these exist because prompt-level instructions drift. Do not move any of them into a prompt.
8. The agent **proposes, never sends.** Nothing gains write access to Gmail.

---

## 3. Checkpoint 0 — all three, together (~45 min)

Pair on this, land it on `main`, tag it `ctx-contract-v1`. Everyone branches from that tag.

### 3.1 `models/schema.py` — new frozen dataclasses

Append only; do not modify existing ones. Same style as the file already uses (stdlib dataclasses, tz-aware UTC datetimes, no Pydantic).

```python
class EntityKind(str, Enum):
    PERSON = "person"
    ORG = "org"
    CASE = "case"              # ticket / case / incident — the CRM-shaped thing
    PROJECT = "project"
    DELIVERABLE = "deliverable"
    DOCUMENT = "document"      # invoice, PO, spec, contract
    TOPIC = "topic"

class ChunkKind(str, Enum):
    BODY = "body"
    QUOTED = "quoted"
    SIGNATURE = "signature"

class MentionSource(str, Enum):
    HEADER = "header"          # free + exact
    REGEX = "regex"            # free + exact
    LLM = "llm"                # fuzzy

@dataclass Chunk:
    chunk_id: str; email_id: str; ord: int; text: str; kind: ChunkKind

@dataclass Entity:
    entity_id: str; kind: EntityKind; canonical_name: str; normalized_key: str
    aliases: list[str] = []; first_seen: datetime; last_seen: datetime
    mention_count: int = 0; salience: float = 0.0

@dataclass Mention:
    email_id: str; chunk_id: Optional[str]; entity_id: str; span_text: str
    confidence: float; source: MentionSource

@dataclass Relation:
    src_entity_id: str; dst_entity_id: str; rel: str      # belongs_to|participant_in|mentions|owner_of
    weight: float; evidence_email_ids: list[str] = []

@dataclass Brief:
    node_type: str            # thread|case|project|person
    node_id: str; headline: str; body_md: str
    open_items: list[str] = []; evidence_email_ids: list[str] = []
    evidence_hash: str; generated_at: datetime

@dataclass ContextSection:
    label: str; text: str; source_email_ids: list[str] = []; score: float = 0.0

@dataclass ContextPack:
    query: Optional[str]; anchor_email_id: Optional[str]
    sections: list[ContextSection] = []; total_chars: int = 0
```

### 3.2 `models/db.py` — new `CONTEXT_SCHEMAS`

Add a new module constant and append it to `ALL_SCHEMAS`. `prepare()` already iterates `schemas or ALL_SCHEMAS`, so this is non-breaking for the callers that pass explicit schemas (`ingestion/store.py`, `pipeline/persist.py`).

Eleven tables:

| Table | Purpose | Notes |
|---|---|---|
| `chunk` | `chunk_id` PK, `email_id`, `ord`, `text`, `kind` | index on `email_id` |
| `chunk_fts` | FTS5 virtual, external-content over `chunk` | plus the 3 sync triggers |
| `chunk_vec` | `chunk_id` PK, `dim` INT, `vec` BLOB | float32 little-endian |
| `entity` | `entity_id` PK, `kind`, `canonical_name`, `normalized_key`, `first_seen`, `last_seen`, `mention_count`, `salience` | unique index on `(kind, normalized_key)` |
| `entity_alias` | `entity_id`, `alias`, `normalized_alias` | index on `normalized_alias` |
| `entity_vec` | `entity_id` PK, `vec` BLOB | for resolution |
| `mention` | `mention_id` PK, `entity_id`, `email_id`, `chunk_id`, `span_text`, `confidence`, `source` | indexes on `email_id` and `entity_id` |
| `relation` | `src_entity_id`, `dst_entity_id`, `rel`, `weight`, `evidence_email_ids` JSON | PK `(src, dst, rel)` |
| `node_brief` | `node_type`, `node_id` PK, `headline`, `body_md`, `open_items` JSON, `evidence_email_ids` JSON, `evidence_hash`, `generated_at` | `evidence_hash` is what makes rebuilds incremental |
| `agent_conversation` | `conversation_id` PK, `title`, `created_at`, `updated_at` | Person C |
| `agent_message` | `id` PK AUTOINCREMENT, `conversation_id`, `role`, `content` JSON, `created_at` | Person C |

Follow the file's existing style: `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, JSON stored as TEXT blobs. New tables need no `MIGRATIONS` entries — that dict is only for columns added to already-shipped tables.

### 3.3 `pipeline/orchestrate.py` — stage split + DI slots

```python
CONTEXT_STAGES: Sequence[str] = ("chunk", "embed", "extract")

STAGES: Sequence[str] = (                       # unchanged
    "classify", "score", "summarize", "categorize",
    "scheduling", "calendar", "propose_event", "outline",
)

ALL_STAGE_NAMES = tuple(CONTEXT_STAGES) + tuple(STAGES)
```

Add three optional callables to `Pipeline.__init__` (`chunk`, `embed`, `extract`) and wire them in `with_defaults` with deferred imports, matching the existing pattern exactly. Add a `ContextPipeline` class — or a `stages=CONTEXT_STAGES` mode on the existing one — that runs only the context stages. Reuse `_run_stage`: a chunking failure on email 47 must not cost the other 159 their processing.

### 3.4 `pipeline/incremental.py` — teach it the new stages

Add `_STAGE_OUTPUT` entries so an email that already has chunks/vectors/mentions is skipped. Read-status flips must **not** invalidate context stages — body content didn't change.

### 3.5 `llm/config.py`

Add `"extract"`, `"brief"`, `"agent"` to `ROUTABLE_STAGES`. The per-stage `LLM_PROVIDER_<STAGE>` / `LLM_MODEL_<STAGE>` resolution already handles the rest.

### 3.6 `interfaces/README.md`

Document the signatures for `/context/`, `/retrieval/`, `/agent/` (listed in §4–§6) so each track can stub the others.

### 3.7 `requirements.txt`

Add `numpy>=1.24`. Also add `requests>=2.31` — `llm/ollama.py:23` already imports it but it's undeclared, arriving only transitively via `google-auth`.

**Checkpoint 0 done when:** `python -c "from models.db import init_db; init_db()"` creates all 11 tables, `pytest -q` still passes 463 tests, and the tag is pushed.

---

## 4. Person A — Context substrate (write side)

**Branch:** `track-a-context`  **Owns exclusively:** `/context/`, `llm/embeddings.py`

You are the foundation. B and C build against fixtures, so you are not blocking them — but your output quality determines everything downstream. Land the CLI early so quality is inspectable.

### Tasks in order

**A1 — `context/chunk.py`**

```python
def chunk_email(raw: RawEmail, *, target_chars: int = 800, overlap: int = 100) -> list[Chunk]
```

**Strip quoted reply history and signatures first. This is load-bearing.** Unstripped quote blocks make every email in a thread look near-identical, which poisons embeddings (everything is 0.95 similar) and extraction (entities get attributed to the wrong message). Detect:

- `On <date>, <name> wrote:` and locale variants
- Runs of `>`-prefixed lines
- `-----Original Message-----`
- Signature delimiter `-- ` on its own line, and trailing blocks that are mostly contact details

Emit stripped content as `ChunkKind.BODY`; keep the removed spans as `QUOTED` / `SIGNATURE` chunks (stored, but excluded from embedding and extraction) so nothing is silently lost. Split `BODY` on paragraph boundaries to ~800 chars with 100 overlap; never split mid-sentence.

**A2 — `llm/embeddings.py`**

```python
def embed_texts(texts: Sequence[str], *, model: str = "nomic-embed-text") -> list[bytes]
def cosine(a: bytes, b: bytes) -> float
def cosine_matrix(query: bytes, matrix: "np.ndarray") -> "np.ndarray"
def to_blob(vec) -> bytes   /   def from_blob(blob: bytes) -> "np.ndarray"
```

POST to ollama `/api/embed`, batched. Mirror the transport style of `llm/ollama.py:78` (timeout from `OLLAMA_TIMEOUT`, host from `OLLAMA_HOST`) and give it a `check()` like `llm/ollama.py:201` that distinguishes "server down" from "model not pulled" — you'll want that error message the first time someone forgets the `ollama pull`.

Store as float32 little-endian. **Normalize on write** so cosine is a plain dot product at query time.

**A3 — `context/extract.py`**

```python
def extract_entities(raw: RawEmail, chunks: Sequence[Chunk]) -> list[Mention]
```

**Deterministic first, LLM second** — matching this repo's philosophy that hard rules live in code.

*Free/exact pass (no model):*
- `PERSON` and `ORG` from `raw.sender`, `raw.recipients`, and `headers` (Cc/Reply-To). Org from the email domain, minus free-mail providers. Reuse `_addr_only` from `scoring/signals.py:36` — do not write a second address parser.
- `CASE` / `DOCUMENT` IDs by regex: `[A-Z]{2,10}-\d{1,6}`, `#\d{3,}`, and `INV-` / `PO-` / `ORD-` / `CASE-` forms. Also scan the subject, including `Re:`/`Fwd:` prefixes.

*LLM pass (one call, `LLM_MODEL_EXTRACT`):* `PROJECT`, `DELIVERABLE`, `TOPIC`, and — importantly — **which of the regex-found IDs is this email's actual subject vs. an incidental mention** (a signature footer, a quoted ticket link). Schema with `reason` declared first, then the arrays. Cap `maxLength` on every string.

Give the model the subject, the `BODY` chunks only, and the list of IDs the regex pass already found. Do not re-ask it for things regex got right.

**A4 — `context/resolve.py`**

```python
def resolve(mentions: Sequence[Mention], existing: EntityIndex) -> ResolveResult
```

Deterministic ladder, in order — first match wins:

1. Exact `normalized_key` match within the same `kind`
2. `entity_alias` match
3. Same-kind embedding cosine ≥ **0.86** against `entity_vec`
4. Otherwise, a new entity

Normalization: lowercase, strip punctuation and articles, collapse whitespace, singularize trailing `s` for org/project names. Person entities key on the bare address, never the display name.

**This module must be pure and unit-testable with no model** — pass embeddings in as an argument. The 0.86 threshold is a starting guess; it is the first knob to tune after A6.

**A5 — `context/store.py` and `context/consolidate.py`**

```python
# store.py
def upsert_chunks(chunks, *, db_path=None) -> None
def upsert_vectors(pairs, *, db_path=None) -> None
def upsert_entities(entities, *, db_path=None) -> None
def upsert_mentions(mentions, *, db_path=None) -> None
def entities_for_email(email_id, *, db_path=None) -> list[Entity]
def emails_for_entity(entity_id, *, db_path=None) -> list[str]
def neighbors(entity_id, *, hops: int = 1, db_path=None) -> list[Entity]
def load_all_vectors(*, db_path=None) -> tuple[list[str], "np.ndarray"]   # B needs this

# consolidate.py
def consolidate(db_path=None) -> ConsolidateStats
```

`consolidate` resolves pending mentions, then derives `relation` edges:
- `PERSON --participant_in--> CASE|PROJECT` from co-occurrence, weighted by mention count
- `CASE --belongs_to--> PROJECT` when a project entity co-occurs with a case across ≥2 emails
- Symmetric `mentions` edges for everything else

Then mark affected `node_brief` rows dirty by recomputing `evidence_hash` (sorted `email_id`s + their `processed_at`). Person B consumes that flag.

Use `models.db.connect` / `prepare` for every connection.

**A6 — `context/cli.py` — the inspection gate**

```bash
python -m context.cli graph             # entity counts by kind; top projects w/ their cases and people
python -m context.cli entities --kind case
python -m context.cli email <email_id>  # what was extracted from one email, by source
python -m context.cli chunks <email_id> # verify quote-stripping actually worked
```

**This is the go/no-go for the whole project.** Before B's retrieval or C's agent means anything, `graph` has to show recognizable cases and projects on the real 160 emails. If cases come out fragmented — one node per mention — the A4 cosine threshold and normalization are wrong. Fix that here, not later.

**A7 — Tests.** `context/tests/`: quote-stripping against real forwarded/replied fixtures; regex ID extraction; the full resolution ladder with hand-built vectors; store round-trips. Follow the existing `calendaring/tests/fakes.py` pattern — no network, no model.

**Done when:** `python -m context.cli graph` on the 160-email corpus shows correct cases/projects/people, chunk counts look sane (~8–12 per email, quotes excluded), and `pytest context/` is green.

---

## 5. Person B — Retrieval, briefs, context-aware generation

**Branch:** `track-b-retrieval`  **Owns exclusively:** `/retrieval/`, plus edits to `drafting/outline.py`, `summarization/summarize.py`, `scoring/signals.py`

**Do not wait on Person A.** Build `retrieval/tests/fixtures.py` first — a small in-memory DB with ~12 synthetic emails, 3 cases, 2 projects, hand-written vectors — following the pattern of `interface/fixtures.py`. Everything below is testable against it.

### Tasks in order

**B1 — `retrieval/tests/fixtures.py`** (do this first, it unblocks you)

**B2 — `retrieval/search.py`**

```python
@dataclass ScoredChunk: chunk_id, email_id, text, score, channel

def search(query: Optional[str], *, k: int = 12, anchor_email_id: Optional[str] = None,
           filters: Optional[dict] = None, db_path=None) -> list[ScoredChunk]
```

Three private channels, each returning its own ranked list:

- `_bm25(query, k)` — FTS5 `MATCH` with `bm25()` ordering over `chunk_fts`, `kind = 'body'` only.
- `_vector(query, k)` — embed the query via `llm.embeddings.embed_texts`, load vectors once via `context.store.load_all_vectors`, dot product (they're pre-normalized). **Cache the matrix at module level**; reloading 4.6 MB per call is the obvious performance mistake here.
- `_graph(anchor_email_id, hops=2)` — `entities_for_email` → `neighbors` → `emails_for_entity` → their chunks. Score by entity salience × edge weight, decayed per hop.

Fuse with **Reciprocal Rank Fusion** (`score = Σ 1/(60 + rank)`), not weighted score-sum — the three channels' raw scores aren't commensurable. No channel gets to dominate.

**B3 — `retrieval/pack.py`**

```python
def build_pack(*, anchor_email_id=None, query=None, budget_chars: int = 6000,
               db_path=None) -> ContextPack
```

Fixed priority order, filling until the budget is spent:

1. Anchor email's own summary + subject
2. Thread brief (if the thread has >1 message)
3. Case / project briefs for the anchor's entities
4. Open items from those briefs
5. Top-k foreign chunks from `search()`

Every foreign chunk section gets a label carrying its provenance — `From <sender>, <date>, re: <subject>` — so the model can cite rather than blur. Deduplicate by `email_id` and never include the anchor email's own chunks as "foreign."

**This is the single entry point every consumer uses.** Nobody builds context strings by hand.

**B4 — `retrieval/briefs.py`**

```python
def rebuild_dirty(db_path=None, *, limit: Optional[int] = None) -> int
def get_brief(node_type: str, node_id: str, db_path=None) -> Optional[Brief]
```

For each dirty node, one call on `LLM_MODEL_BRIEF` producing `headline`, `body_md`, `open_items`. Schema with `reason` first.

Two gates that matter: only build briefs for nodes with **≥2 emails** (or 160 single-email briefs get generated for nothing), and skip when `evidence_hash` is unchanged. Brief content is a *state* document — what's happened, who's involved, what's still open, what was decided — not a summary of each email in turn.

**B5 — Rewire the consumers. Additive optional parameters only.**

```python
# drafting/outline.py
def generate_reply_outline(processed, raw, context: Optional[ContextPack] = None)

# summarization/summarize.py
def summarize(email, context: Optional[ContextPack] = None)
```

When `context` is present, insert a `What you already know:` section above the body in the user message. Extend `OUTLINE_SYSTEM_PROMPT` (`drafting/outline.py:37`) with two sentences: draw on the prior context to make bullets concrete and specific; never invent facts absent from both the email and the context.

**Do not touch `is_eligible` at `drafting/outline.py:71`.** The read-status / no-reply gate stays exactly as it is.

Because the parameter is optional and defaults to `None`, every existing test in `drafting/tests/test_outline_gating.py` (327 lines) keeps passing unchanged. Verify that before you push.

**B6 — Fix the dead scoring signals**

`scoring/signals.py:20` hardcodes `ACCOUNT_OWNER = "iamsamkitshah@gmail.com"` and `DEFAULT_VIP_SENDERS = frozenset()`. The mailbox has since changed, so `is_direct` is computed against the wrong address and `is_vip` is always `False` — **two of the five within-band scoring weights (0.30 + 0.20 = half the weight) are currently dead.** Now that the graph exists:

- Derive owner identity from ingestion (the authenticated Gmail profile) rather than a constant.
- Derive VIP from graph interaction frequency — people above a percentile of two-way exchange volume.

Small, in scope, and it makes the ranking you already have work as designed.

**B7 — `retrieval/cli.py`**

```bash
python -m retrieval.cli search "henderson escalation"
python -m retrieval.cli pack --email <email_id>     # see exactly what an outline will be given
python -m retrieval.cli brief case <entity_id>
```

`pack --email` is the debugging tool you'll use most — it shows the literal context an outline receives.

**Done when:** `search` returns hits from emails that don't contain the query's words; `pack --email` produces a coherent, budget-respecting, cited context; `pytest -q` still passes all 463 existing tests plus your new ones.

---

## 6. Person C — The agent and the extension

**Branch:** `track-c-agent`  **Owns exclusively:** `/agent/`, `api/main.py`, `extension/`

**Do not wait on Person B.** Stub `search_context` to return a hand-written `ContextPack` fixture and build the whole loop and UI against it. Swap in the real `build_pack` at integration.

### Tasks in order

**C1 — `agent/tools.py`**

```python
TOOL_SPECS: list[dict]                       # Anthropic tool-definition format
def dispatch(name: str, args: dict, *, db_path=None) -> dict
```

| Tool | Backed by |
|---|---|
| `search_context(query, k?)` | `retrieval.pack.build_pack` |
| `get_email(email_id)` | `pipeline.persist.get` + `ingestion.store.get` |
| `get_thread_brief(thread_id)` | `retrieval.briefs.get_brief` |
| `get_entity_brief(entity_id)` | `retrieval.briefs.get_brief` |
| `list_entities(kind?, query?)` | `context.store` |
| `find_open_items(person?, case?)` | `node_brief.open_items` |
| `list_queue(filters)` | **reuse `api/filters.py`** — do not reimplement filtering |
| `draft_reply(email_id, instructions)` | **reuse `drafting/expand.py`** |
| `summarize_selection(email_ids)` | `retrieval.pack` + one call |

Keep every tool result JSON-serializable and bounded — cap list results and truncate long text, or the loop blows its context on turn three.

**C2 — `agent/loop.py`**

```python
def run(messages: list[dict], *, max_turns: int = 8, db_path=None) -> Iterator[Event]
```

Standard Anthropic tool-use loop: call with `tools=TOOL_SPECS`, while `stop_reason == "tool_use"` dispatch each `tool_use` block and append a `tool_result`, repeat to `max_turns`. Yield events (`text_delta`, `tool_start`, `tool_end`, `done`) so the transport can stream.

Get the model via `llm.client.get_client("agent")`. **Raise a clear error if the resolved provider is ollama** — `llm/ollama.py` has no `tools` support and would silently ignore them, producing a confidently wrong answer with no tool calls. Fail loudly instead.

System prompt: you are Valence, an assistant over this user's mailbox; always ground answers in tool results and cite `email_id`s; never claim to have sent anything; when asked to draft, return the draft for review.

**C3 — `agent/conversation.py`** — persist to `agent_conversation` / `agent_message` so the panel survives Gmail's SPA navigation (it remounts constantly; in-memory state will not survive).

**C4 — `api/main.py`**

```
POST /api/agent/chat                 -> streaming
GET  /api/agent/conversations/{id}
```

Follow the existing endpoint style — module-level `DB_PATH` so tests can override, and the same error-shape conventions as the existing 12 endpoints.

**C5 — `extension/background.js` — streaming**

`chrome.runtime.sendMessage` cannot stream. Use `chrome.runtime.connect`: the content script opens a port, the service worker fetches, reads the `ReadableStream`, and posts chunks over the port. With up to 8 tool turns, a response can take 20 s+, so a blocking spinner feels broken.

Keep the existing message-based proxy for all non-streaming calls — it exists to bypass Gmail's CSP and still works.

*Fallback, if MV3 streaming fights back:* non-streaming POST with a spinner and a tool-progress line. Ship that rather than sink the day into it.

**C6 — `extension/content/ask.js`** — a third tab, "Ask," beside the existing Email and Inbox tabs in the panel. Message list, input, streaming text, and a collapsed "used N sources" line listing cited emails — clicking one sets `location.hash = "#all/" + threadId`, matching the existing navigation at `detail.js:288-367`. All text through `textContent`, never `innerHTML` (the rule noted at `detail.js:22`).

**C7 — `extension/content/detail.js` — two additions**

*Context section:* linked case/project chips + related emails for the open message. This is what makes the graph **visible** rather than merely felt — without it, users can't tell the correlation is working.

*Feedback controls, ported from `api/static/index.html:578`:* the segmented level picker and the automated/real toggle, hitting the existing `POST /api/emails/{id}/feedback`. **This port is required, not optional.** Dropping the webapp removes the only UI for the sender-priors loop shipped in HEAD (`698aba4`) — without it, that feature has no way to be used at all.

`api/static/index.html` stays on disk as an unmaintained debug view. No further investment in it.

**C8 — Tests.** `agent/tests/`: tool dispatch with a fake DB; the loop against a scripted fake client that returns canned `tool_use` blocks (no network); `api/tests/` additions for the two new endpoints, following `api/tests/test_api.py`.

**Done when:** the Ask tab answers "summarize everything about the Henderson case" with cited emails, "draft a reply to this saying we need until Friday" returns a reviewable draft, and the feedback picker persists across a refresh.

---

## 7. Merge order and integration

```
Checkpoint 0 (tagged)
      │
      ├── track-a-context ───┐
      ├── track-b-retrieval ─┼── merge A → merge B → merge C → integration run
      └── track-c-agent ─────┘
```

Develop fully in parallel; only the merges are ordered. If A slips, B and C are still complete and green against fixtures.

At each merge: `pytest -q` on `main`, not just on the branch.

Integration steps: swap B's fixture DB for the real graph, swap C's stubbed `search_context` for `build_pack`, then run §8 top to bottom.

---

## 8. Verification

```bash
ollama pull nomic-embed-text
pip install -r requirements.txt

# 1. Get the real corpus in — only 2 emails are currently ingested
python -m ingestion.cli ingest --limit 200
python -m ingestion.cli count                  # expect ~160

# 2. Build the graph, then INSPECT IT before trusting anything downstream
python -m pipeline.cli process
python -m context.cli graph
python -m context.cli chunks <some_email_id>   # quote-stripping actually worked?
```

**Step 2 is the go/no-go.** Expect `CASE` nodes for the ticket IDs in the test mail, `PROJECT` nodes grouping them, `PERSON` nodes with plausible counts. If cases are fragmented — one node per mention — tune `context/resolve.py`'s cosine threshold and normalization **here**. Everything downstream inherits this; do not proceed past a bad graph.

```bash
# 3. Retrieval quality
python -m retrieval.cli search "henderson escalation"
python -m retrieval.cli pack --email <email_id>
python -m retrieval.cli brief case <entity_id>
```

Expect hits from emails that never use the query's words. That's the whole point — if every hit is a keyword match, the graph and vector channels aren't contributing and the RRF fusion needs checking.

```bash
# 4. The actual payoff
python -m pipeline.cli process --only outline
python -m interface.review_cli --limit 10
```

Compare an outline against its pre-change version for the same email. The new one should reference facts that live in **other** emails. If it doesn't, check `retrieval/cli.py pack --email` for that message — the context is either empty or wasn't threaded through.

```bash
# 5. Extension
uvicorn api.main:app --reload
```

Reload the unpacked extension, open Gmail, and check: case/project chips in the detail panel; the Ask tab answering a cross-thread question with citations; the feedback level picker present and persisting through a refresh.

```bash
# 6. Regression
pytest -q      # all 463 existing tests must still pass
```

---

## 9. Cost and risk

| | |
|---|---|
| One-time extraction | ~160 Claude calls on `claude-sonnet-5`. Opus is unnecessary for span extraction. |
| Embeddings | Local, free, ~1,500 texts. |
| Briefs | ~30–60 calls initially, then only dirty nodes. |
| Steady state | One extraction call per new email + affected briefs. |

**Primary risk — entity resolution over- or under-merging.** Mitigated by keeping `resolve.py` deterministic and unit-testable, and by making `context.cli graph` the mandatory gate before anything depends on the graph.

**Secondary risk — MV3 service-worker streaming.** Documented fallback: non-streaming with a spinner. Timebox it.

**Third risk — quote-stripping.** If it under-strips, every chunk in a thread embeds near-identically and retrieval degrades to noise. `context.cli chunks <email_id>` on a deeply-nested forward is the check; do it early.
