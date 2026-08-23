# Valence — Context Graph + In-App Agent

A parallel build run sheet for three people. Every task below is a **prompt you paste into Claude Code**, not code you write by hand.

`PHASES.md` phases 0–8 are built and shipped. This is the next architectural layer on top of them.

---

## 0. How to use this doc

1. Read **§2 (Why this design)** — all three of you. It's the shared mental model; without it you'll each make different guesses at integration.
2. Run the table in **§1** top to bottom. Your column is yours; ignore the other two.
3. At the start of **every** Claude session, paste the **standing context block** in §3 first. Then paste your prompt.
4. Prompts are numbered (`CP1`, `A3`, `B5`…). The table is the only place that says what order they go in.

Each prompt is self-contained — Claude has no memory of the other two people's sessions.

---

## 1. Master run order

### Phase 0 — Together, in one room, one screen (~45 min)

**Do not parallelize this.** One person drives, other two watch. It's the only shared surface; two people editing `models/schema.py` on separate branches costs you the afternoon.

| # | Who | Prompt | Produces | Blocked by |
|---|---|---|---|---|
| CP1 | All 3, pairing | Schema + tables | `models/schema.py`, `models/db.py` | — |
| CP2 | All 3, pairing | Pipeline stage split | `pipeline/orchestrate.py`, `pipeline/incremental.py` | CP1 |
| CP3 | All 3, pairing | Config, deps, interface doc | `llm/config.py`, `requirements.txt`, `interfaces/README.md` | CP1 |
| CP4 | All 3, pairing | Verify + tag `ctx-contract-v1` | a tag everyone branches from | CP2, CP3 |

### Phase 1 — Parallel. Three people, three branches, no waiting.

Each column runs **top to bottom in order**. The three columns run **at the same time**. Nobody in one column ever waits on another column.

| Order | 🅐 Person A — `track-a-context` | 🅑 Person B — `track-b-retrieval` | 🅒 Person C — `track-c-agent` |
|---|---|---|---|
| 1 | **A1** Chunking + quote stripping | **B1** Fixture graph DB *(do first — unblocks you)* | **C1** Agent tools |
| 2 | **A2** Local embeddings client | **B2** Hybrid search (BM25+vector+graph) | **C2** Tool-use loop |
| 3 | **A3** Entity extraction | **B3** `build_pack()` — the one entry point | **C3** Conversation persistence |
| 4 | **A4** Entity resolution | **B4** Rollup briefs | **C4** API endpoints |
| 5 | **A5** Graph store + consolidate | **B5** Rewire outline + summarize | **C5** Extension streaming transport |
| 6 | **A6** Inspection CLI | **B6** Fix the dead scoring signals | **C6** "Ask" tab |
| 7 | **A7** Tests + threshold tuning | **B7** Retrieval CLI | **C7** Context chips + feedback port |
| 8 | — | — | **C8** Tests |

**Owned exclusively.** Nobody edits outside their own list:

| | Folders | Also edits |
|---|---|---|
| 🅐 A | `/context/` | `llm/embeddings.py` |
| 🅑 B | `/retrieval/` | `drafting/outline.py`, `summarization/summarize.py`, `scoring/signals.py` |
| 🅒 C | `/agent/`, `/extension/` | `api/main.py` |

If a change needs a file you don't own — **stop and talk**, don't merge through it.

### Phase 2 — Merge, in this order

| # | Who | Prompt | Blocked by |
|---|---|---|---|
| M1 | A | Merge `track-a-context` → `main` | A7 |
| M2 | B | Merge `track-b-retrieval` → `main` | M1, B7 |
| M3 | C | Merge `track-c-agent` → `main` | M2, C8 |

`pytest -q` on `main` after each merge, not just on the branch.

### Phase 3 — Integration

| # | Who | Prompt | Blocked by |
|---|---|---|---|
| INT1 | Whoever's free | Swap B's fixtures + C's stubs for the real thing | M3 |
| INT2 | All 3 | Run §9 verification top to bottom | INT1 |

---

## 2. Why this design

### The problem

Every LLM call in this repo currently sees **exactly one email and nothing else**:

- `summarization/summarize.py:52` — identity header + body.
- `drafting/outline.py:96` — identity header + this email's own summary + its own body.
- `scoring/score.py:180` — sender/subject/truncated body + its own rule signals.

`thread_id` is stored and indexed on both tables but **never joined on** to gather sibling messages; its only consumer is `pipeline/staleness.py`. No embeddings, no vector store, no FTS5 index, no chunk or entity table exists anywhere. `api/filters.py:58` "search" is a Python substring scan over every row loaded into memory.

Outlines are generic *by construction*. We ask the model to write a specific reply while showing it a single message stripped of every fact that would make it specific.

### The substrate choice

We evaluated vector DB / tree / embeddings tree / working tree. **None alone. We're building an entity-centric graph in SQLite, with embeddings and FTS5 as two retrieval channels into it, plus hierarchical rollup briefs as working memory.**

**Not a pure vector database.** What we need is a *join on identity, not a join on similarity*. Two emails belong to the same case even when they share almost no vocabulary — one says "the Henderson escalation," another says "ticket 4471," a third is a forwarded invoice with a PO number. Cosine similarity scores those three as unrelated. An exact key — case ID, participant set, project name — links them with certainty. At 160 emails there also isn't enough text for semantic similarity to be the differentiator.

**Not a pure tree.** Email context isn't a tree. One email touches several cases; one person spans many projects; a thread forks into two workstreams. Forcing a tree means picking one parent and discarding the other edges — exactly the correlation we're trying to capture. It's a DAG.

**Embeddings still earn their place,** for two specific jobs: entity resolution (deciding "Henderson escalation" and "Henderson issue" are one node) and fallback recall when no shared identifier exists. A channel into the graph, not the architecture of it.

**"Working tree" = the `node_brief` table.** A cached, LLM-written state document per thread / case / project / person, regenerated only when its evidence set changes. Roll-ups *are* tree-shaped (email → thread → case → project) even though the edges underneath are a graph.

**Sizing.** ~160 emails ≈ ~1,500 chunks × 768 dims float32 ≈ 4.6 MB. Brute-force cosine ≈ 5 ms in numpy. No ANN index, no vector DB.

### Right fix vs. band-aid

The band-aid would be stuffing the whole thread into the outline prompt — ~20 lines, visibly better on multi-message threads, and **not what was asked for**: it does nothing for correlation across *separate* threads, which is the entire premise. The retrieval substrate is the real fix and subsumes the band-aid, since thread history is just the cheapest edge in the graph.

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

If extraction runs in the same per-email pass as outline generation, email #1's outline can't see email #160's case; the graph isn't built yet. So `pipeline/refresh.py::process_incremental` splits into: **context pass** (chunk/embed/extract per email) → **consolidate** (corpus-wide entity resolution + dirty briefs) → **reasoning pass** (the existing 8 stages, now with a populated graph) → **existing post-passes** unchanged.

---

## 3. Standing context block

**Paste this at the top of every Claude Code session, before your task prompt.** It carries the conventions that are easy to violate and expensive to unwind.

```
You are working in the Valence repo (AI email agent, Gmail + Google Calendar).
Read CLAUDE.md, CONTEXT.md, and PHASES-COMPLEX.md before you start.

Standing rules for this session:

1. I own specific folders. Do not edit any file outside the ones my task names.
   If you think a change requires touching another file, STOP and tell me instead
   of doing it.

2. Reuse before you create. Specifically:
   - every DB connection goes through models.db.connect / models.db.prepare
   - every model call goes through llm.client.get_client(stage)
   - every From/To prompt header uses llm.prompting.email_identity_block
   - email address parsing reuses _addr_only from scoring/signals.py:36
   Search for an existing helper before writing a new one.

3. Every JSON schema for a model call MUST declare its `reason` field BEFORE the
   answer field. Constrained decoding emits fields in declaration order, so
   reason-first informs the answer instead of rationalizing it. See the comment
   at scoring/score.py:92. Put a maxLength on every string field.

4. Hard rules live in code, never in prompts. Do not move any existing gate into
   a system prompt. Specifically do not touch: drafting/outline.py:71
   (is_eligible), scoring/score.py:226 (no-reply level cap), feedback/apply.py.

5. When adding a parameter to an existing function, it must be optional with a
   default that preserves current behavior. All 463 existing tests must still
   pass. Run `pytest -q` before you tell me you're done.

6. Environment facts — do not re-derive or work around these:
   - Python 3.9.6. sqlite3 enable_load_extension is UNAVAILABLE, so sqlite-vec
     and sqlite-vss cannot load. Vectors are float32 BLOBs + numpy.
   - FTS5 IS available and works.
   - Embeddings are local via ollama nomic-embed-text. Reasoning is Claude API.
   - llm/ollama.py has NO tool-calling support.

7. Write tests with no network and no model calls, following the pattern in
   calendaring/tests/fakes.py.

Confirm you've read the files and understood the rules, then wait for my task.
```

---

## 4. Phase 0 prompts — Checkpoint 0

Prerequisites, run once by everyone before CP1:

```bash
ollama pull nomic-embed-text        # 274 MB, 768-dim. Required — not currently pulled.
ollama list                         # confirm nomic-embed-text AND a chat model are present

export LLM_PROVIDER=anthropic
export LLM_MODEL_EXTRACT=claude-sonnet-5     # span extraction — opus is overkill
export LLM_MODEL_BRIEF=claude-sonnet-5
export LLM_MODEL_AGENT=claude-opus-5
export EMAIL_AGENT_DB=./ingestion/data/emails.db
```

### CP1 — Schema and tables

```
Read models/schema.py and models/db.py in full first.

Add new frozen dataclasses to models/schema.py. APPEND ONLY — do not modify any
existing dataclass or enum. Match the file's existing style exactly: stdlib
dataclasses, no Pydantic, all datetimes timezone-aware UTC.

New enums:
  EntityKind: person, org, case, project, deliverable, document, topic
    ("case" is the CRM-shaped thing — ticket / case / incident)
  ChunkKind: body, quoted, signature
  MentionSource: header, regex, llm

New dataclasses:
  Chunk    — chunk_id, email_id, ord, text, kind
  Entity   — entity_id, kind, canonical_name, normalized_key, aliases,
             first_seen, last_seen, mention_count, salience
  Mention  — email_id, chunk_id (optional), entity_id, span_text, confidence, source
  Relation — src_entity_id, dst_entity_id, rel, weight, evidence_email_ids
             (rel is one of: belongs_to, participant_in, mentions, owner_of)
  Brief    — node_type (thread|case|project|person), node_id, headline, body_md,
             open_items, evidence_email_ids, evidence_hash, generated_at
  ContextSection — label, text, source_email_ids, score
  ContextPack    — query, anchor_email_id, sections, total_chars

Then in models/db.py, add a new module constant CONTEXT_SCHEMAS and append it to
ALL_SCHEMAS. Use the file's existing style: CREATE TABLE IF NOT EXISTS,
CREATE INDEX IF NOT EXISTS, JSON stored as TEXT blobs.

Eleven tables:
  chunk       — chunk_id PK, email_id, ord, text, kind. Index on email_id.
  chunk_fts   — FTS5 virtual table, external-content over chunk, plus the three
                sync triggers (insert/update/delete).
  chunk_vec   — chunk_id PK, dim INT, vec BLOB (float32 little-endian).
  entity      — entity_id PK, kind, canonical_name, normalized_key, first_seen,
                last_seen, mention_count, salience.
                UNIQUE index on (kind, normalized_key).
  entity_alias— entity_id, alias, normalized_alias. Index on normalized_alias.
  entity_vec  — entity_id PK, vec BLOB.
  mention     — mention_id PK, entity_id, email_id, chunk_id, span_text,
                confidence, source. Indexes on email_id and on entity_id.
  relation    — src_entity_id, dst_entity_id, rel, weight,
                evidence_email_ids JSON. PK (src, dst, rel).
  node_brief  — node_type, node_id PK, headline, body_md, open_items JSON,
                evidence_email_ids JSON, evidence_hash, generated_at.
  agent_conversation — conversation_id PK, title, created_at, updated_at.
  agent_message      — id PK AUTOINCREMENT, conversation_id, role,
                       content JSON, created_at.

Do NOT add MIGRATIONS entries. That dict is only for columns added to tables
that already shipped; these are all new tables.

When done: run `python -c "from models.db import init_db; init_db()"` and show
me `.schema` output proving all 11 tables exist.
```

### CP2 — Pipeline stage split

```
Read pipeline/orchestrate.py and pipeline/incremental.py in full first.

In pipeline/orchestrate.py:
  - Add CONTEXT_STAGES = ("chunk", "embed", "extract") as a new module constant.
  - Leave the existing STAGES tuple completely unchanged.
  - Add ALL_STAGE_NAMES = tuple(CONTEXT_STAGES) + tuple(STAGES).
  - Add three optional callables to Pipeline.__init__: chunk, embed, extract.
    Follow the existing dependency-injection pattern exactly.
  - Wire them in with_defaults using deferred imports, same as every other stage.
  - They must run through the existing _run_stage wrapper so a chunking failure
    on email 47 doesn't cost the other 159 emails their processing.

In pipeline/incremental.py:
  - Add _STAGE_OUTPUT entries for the three new stages so an email that already
    has chunks / vectors / mentions is skipped on re-run.
  - IMPORTANT: a read-status flip must NOT invalidate the context stages. The
    body content didn't change. Read status only invalidates "outline", as today.

Explain the two-pass ordering back to me before you write anything: why context
stages must complete for the WHOLE corpus before the reasoning stages run for
any email.
```

### CP3 — Config, deps, interface doc

```
Three small changes:

1. llm/config.py — add "extract", "brief", and "agent" to ROUTABLE_STAGES. The
   existing per-stage LLM_PROVIDER_<STAGE> / LLM_MODEL_<STAGE> resolution should
   then handle them with no other change. Verify that by reading provider_for
   and model_for.

2. requirements.txt — add numpy>=1.24. Also add requests>=2.31: llm/ollama.py:23
   already imports it but it is undeclared, arriving only transitively via
   google-auth. That's a latent break.

3. interfaces/README.md — add signature stubs for three new packages so each
   track can build against the others without waiting. Document, do not
   implement:
     context.chunk.chunk_email, context.extract.extract_entities,
     context.resolve.resolve, context.store.* , context.consolidate.consolidate
     llm.embeddings.embed_texts / cosine / to_blob / from_blob
     retrieval.search.search, retrieval.pack.build_pack,
     retrieval.briefs.rebuild_dirty / get_brief
     agent.tools.TOOL_SPECS / dispatch, agent.loop.run
   Match the existing format in that file.
```

### CP4 — Verify and tag

```
Verify Checkpoint 0 is sound before we branch:

1. Run `python -c "from models.db import init_db; init_db()"` against a temp DB
   path and confirm all 11 new tables plus the 3 existing ones are created.
2. Confirm chunk_fts triggers actually fire — insert a chunk row, then MATCH
   against chunk_fts and get it back.
3. Run `pytest -q`. All 463 existing tests must pass. If any fail, the contract
   change broke something — fix it now, not after we branch.
4. Show me a one-page summary of every file changed and what changed in it.

Do NOT commit or tag. I'll do that myself once I've read your summary.
```

Then, by hand:

```bash
git add -A && git commit -m "Freeze the context-graph contract"
git tag ctx-contract-v1 && git push origin main --tags
git checkout -b track-a-context      # each person, their own branch
```

---

## 5. 🅐 Person A — Context substrate

**Branch:** `track-a-context` **Owns:** `/context/`, `llm/embeddings.py`

You're the foundation. B and C build against fixtures so you aren't blocking them — but your output quality determines everything downstream. **A6 is the project's go/no-go gate**; get there fast.

### A1 — Chunking and quote stripping

```
Create context/chunk.py with:

    def chunk_email(raw: RawEmail, *, target_chars: int = 800,
                    overlap: int = 100) -> list[Chunk]

Read ingestion/parse.py first to see what the body text actually looks like
after HTML stripping.

THE MOST IMPORTANT PART — strip quoted reply history and signatures BEFORE
chunking. This is load-bearing, not tidiness: unstripped quote blocks make every
email in a thread look near-identical, which poisons embeddings (everything
scores 0.95 similar) and misattributes entities to the wrong message.

Detect and strip:
  - "On <date>, <name> wrote:" and its common variants
  - runs of lines prefixed with ">"
  - "-----Original Message-----"
  - the "-- " signature delimiter on its own line
  - trailing blocks that are mostly contact details (phone, title, address)

Do not discard what you strip. Emit it as Chunk rows with kind=QUOTED or
kind=SIGNATURE so nothing is silently lost — they're stored but will be excluded
from embedding and extraction later.

Chunk the remaining kind=BODY text on paragraph boundaries to about
target_chars, with overlap between adjacent chunks. Never split mid-sentence.

Tests in context/tests/test_chunk.py: build fixtures for a plain email, a
two-deep reply chain, a forwarded message, and an email that is signature-heavy.
Assert the body chunks contain no quoted text. No network, no model.
```

### A2 — Local embeddings client

```
Create llm/embeddings.py:

    def embed_texts(texts, *, model="nomic-embed-text") -> list[bytes]
    def cosine(a: bytes, b: bytes) -> float
    def cosine_matrix(query: bytes, matrix) -> "np.ndarray"
    def to_blob(vec) -> bytes
    def from_blob(blob: bytes) -> "np.ndarray"
    def check() -> str

Read llm/ollama.py in full first and mirror its transport style — same host from
OLLAMA_HOST, same timeout from OLLAMA_TIMEOUT, same error handling shape.

POST to ollama's /api/embed endpoint, batched (don't make 1500 separate HTTP
calls). Store vectors as float32 little-endian BLOBs.

NORMALIZE VECTORS ON WRITE. That makes cosine a plain dot product at query time,
which is what Person B's search path needs to stay fast.

check() must distinguish "ollama server is not running" from "nomic-embed-text
is not pulled" — model llm/ollama.py:201. Someone will forget the pull and that
error message is what saves them ten minutes.

Tests: round-trip to_blob/from_blob, cosine of a vector with itself is 1.0,
cosine of orthogonal vectors is 0.0. Mock the HTTP call — no network in tests.
```

### A3 — Entity extraction

```
Create context/extract.py:

    def extract_entities(raw: RawEmail, chunks: Sequence[Chunk]) -> list[Mention]

Structure this as DETERMINISTIC FIRST, LLM SECOND. That's this repo's standing
philosophy — see how classification/rules.py runs before
classification/llm_fallback.py.

Pass 1, free and exact, no model:
  - PERSON and ORG entities from raw.sender, raw.recipients, and the Cc /
    Reply-To headers. Derive ORG from the email domain, excluding free-mail
    providers (gmail, outlook, yahoo, icloud, proton...).
    REUSE _addr_only from scoring/signals.py:36. Do not write a second address
    parser.
  - CASE and DOCUMENT IDs by regex: [A-Z]{2,10}-\d{1,6}, #\d{3,}, and the
    INV- / PO- / ORD- / CASE- forms. Scan the subject too, including past any
    Re:/Fwd: prefixes.
  Emit these with source=HEADER or source=REGEX and confidence 1.0.

Pass 2, ONE call on the "extract" stage (routes to LLM_MODEL_EXTRACT):
  Ask only for what regex cannot get — PROJECT, DELIVERABLE, TOPIC — plus one
  judgment call: which of the IDs pass 1 already found is this email's ACTUAL
  subject versus an incidental mention (a signature footer, a quoted ticket
  link, a "see also"). Emit with source=LLM and the model's confidence.

  Give the model: the subject, the kind=BODY chunks only (never QUOTED or
  SIGNATURE), and the list of IDs pass 1 found. Do not re-ask it for anything
  regex already got right.

  Schema: `reason` field declared FIRST, then the arrays. maxLength on every
  string. Follow the pattern in classification/categorize.py:63.

Tests: the full deterministic pass with no model at all; a fake client for the
LLM pass. Assert that quoted-only entities do not become mentions.
```

### A4 — Entity resolution

```
Create context/resolve.py:

    def resolve(mentions, existing: EntityIndex) -> ResolveResult

Deterministic ladder, first match wins, in this exact order:
  1. exact normalized_key match within the same EntityKind
  2. entity_alias match
  3. same-kind embedding cosine >= 0.86 against entity_vec
  4. otherwise, create a new entity

Normalization: lowercase, strip punctuation and leading articles, collapse
whitespace, singularize a trailing "s" for org and project names. PERSON
entities key on the bare email address, NEVER the display name — the same human
shows up as "Sam", "Sam Shah", and "S. Shah" in different messages.

CRITICAL DESIGN CONSTRAINT: this module must be pure and unit-testable with no
model and no DB. Pass embeddings in as an argument rather than computing them
inside. I need to be able to tune the 0.86 threshold against hand-built vectors
in a test, because that number is a guess and it's the first thing I'll change
after seeing real output.

Tests in context/tests/test_resolve.py covering every rung of the ladder plus
the tricky cases: same name different kind (must not merge), alias collision,
and cosine just above/below threshold.
```

### A5 — Graph store and consolidation

```
Create context/store.py and context/consolidate.py.

store.py — thin persistence over the tables from Checkpoint 0. Every connection
goes through models.db.connect / models.db.prepare. Follow the row<->object
mapping style in pipeline/persist.py.

    upsert_chunks(chunks, *, db_path=None)
    upsert_vectors(pairs, *, db_path=None)
    upsert_entities(entities, *, db_path=None)
    upsert_mentions(mentions, *, db_path=None)
    entities_for_email(email_id, *, db_path=None) -> list[Entity]
    emails_for_entity(entity_id, *, db_path=None) -> list[str]
    neighbors(entity_id, *, hops=1, db_path=None) -> list[Entity]
    load_all_vectors(*, db_path=None) -> (list[str], np.ndarray)

load_all_vectors is Person B's hot path — return one contiguous matrix, not a
list of arrays.

consolidate.py:

    def consolidate(db_path=None) -> ConsolidateStats

Corpus-wide pass. Resolve pending mentions via context.resolve, then derive
relation edges:
  - PERSON --participant_in--> CASE|PROJECT from co-occurrence, weight by
    mention count
  - CASE --belongs_to--> PROJECT when a project entity co-occurs with a case
    across 2 or more emails (one co-occurrence is coincidence)
  - symmetric "mentions" edges for everything else

Then mark affected node_brief rows dirty: recompute evidence_hash as a hash of
the sorted email_ids plus their processed_at values. Person B's brief rebuild
reads that flag, so it has to be right — a stale hash means briefs never refresh.
```

### A6 — Inspection CLI ← **the go/no-go gate**

```
Create context/cli.py, following the argparse structure of pipeline/cli.py.

    python -m context.cli graph
        entity counts by kind, then the top projects with their cases and people
        nested underneath. This is the view I use to judge whether the whole
        approach works.

    python -m context.cli entities --kind case
        every case entity with mention count and email count

    python -m context.cli email <email_id>
        what was extracted from one email, grouped by source (header/regex/llm)
        so I can see which pass found what

    python -m context.cli chunks <email_id>
        every chunk with its kind, so I can verify quote-stripping worked

Print human-readable tables, not JSON. I'm reading these with my eyes.

This is the project's go/no-go gate — before Person B's retrieval or Person C's
agent mean anything, `graph` has to show recognizable cases and projects on the
real corpus. Make it good.
```

### A7 — Tests and threshold tuning

```
1. Fill any gaps in context/tests/. Follow calendaring/tests/fakes.py — no
   network, no model calls.
2. Run `pytest -q` — all 463 existing tests plus the new ones must pass.
3. Then run the real thing:
     python -m ingestion.cli ingest --limit 200
     python -m pipeline.cli process
     python -m context.cli graph
     python -m context.cli chunks <pick a forwarded email>

Show me the graph output. If cases come out fragmented — one entity node per
mention instead of one per real case — the cosine threshold in resolve.py or the
normalization is wrong. Tune it and show me before/after. Do not move on from a
bad graph; everything downstream inherits it.
```

---

## 6. 🅑 Person B — Retrieval, briefs, context-aware generation

**Branch:** `track-b-retrieval` **Owns:** `/retrieval/`, plus `drafting/outline.py`, `summarization/summarize.py`, `scoring/signals.py`

**Do B1 first.** It's what lets you build everything else without waiting on Person A.

### B1 — Fixture graph DB

```
Create retrieval/tests/fixtures.py. Follow the pattern in interface/fixtures.py.

Person A is building the real context graph in parallel; I am not waiting on
them. Build a small synthetic one I can develop the whole retrieval layer
against:

  - ~12 emails across 4 threads
  - 3 CASE entities, 2 PROJECT entities, 5 PERSON entities, 2 ORG entities
  - relations: cases belong_to projects, people participant_in cases
  - ~40 chunks with realistic short text
  - hand-written 8-dimensional vectors (NOT 768 — I need to reason about these
    by hand in tests), normalized, arranged so that specific pairs are close and
    others are far
  - 3 node_brief rows

Expose a function that builds all of this into an in-memory SQLite DB and
returns the path, so every test in this package can call it.

Design the fixture around one specific scenario I'll test against repeatedly:
three emails that belong to the same case but share almost NO vocabulary — one
names the case by a human label, one by a ticket ID, one only by participant
overlap. That's the case retrieval has to solve.
```

### B2 — Hybrid search

```
Create retrieval/search.py:

    @dataclass ScoredChunk: chunk_id, email_id, text, score, channel

    def search(query, *, k=12, anchor_email_id=None, filters=None,
               db_path=None) -> list[ScoredChunk]

Three independent private channels, each producing its own ranked list:

  _bm25(query, k)
      FTS5 MATCH with bm25() ordering over chunk_fts, restricted to kind='body'.
      This is the channel that nails exact IDs, names, and numbers.

  _vector(query, k)
      Embed the query via llm.embeddings.embed_texts, load all vectors once via
      context.store.load_all_vectors, then dot product (vectors are pre-
      normalized on write, so no need to normalize again).
      CACHE THE MATRIX AT MODULE LEVEL. Reloading it per call is the obvious
      performance mistake here and it will not show up in tests.

  _graph(anchor_email_id, hops=2)
      entities_for_email -> neighbors -> emails_for_entity -> their chunks.
      Score by entity salience times edge weight, decayed per hop.
      THIS is the channel that produces cross-thread correlation. It's the
      reason the whole project exists — give it real attention.

Fuse with Reciprocal Rank Fusion: score = sum over channels of 1/(60 + rank).
Do NOT use a weighted sum of raw scores — BM25 scores, cosines, and graph
weights are not commensurable and one channel will silently dominate.

Test against the B1 fixture. The key assertion: for the same-case-no-shared-
vocabulary scenario, all three emails come back, and at least one of them comes
from a channel other than BM25.
```

### B3 — build_pack, the single entry point

```
Create retrieval/pack.py:

    def build_pack(*, anchor_email_id=None, query=None, budget_chars=6000,
                   db_path=None) -> ContextPack

This is the ONLY context-assembly function in the codebase. Outline generation,
context-aware summarization, and Person C's agent tool all call it. Nobody
builds context strings by hand. Design it to be the thing three different
callers are happy with.

Fill in this fixed priority order until budget_chars is spent:
  1. the anchor email's own summary and subject
  2. the thread brief, if that thread has more than one message
  3. case and project briefs for the anchor's entities
  4. open_items from those briefs
  5. top-k foreign chunks from search()

Every foreign chunk's ContextSection label must carry provenance:
"From <sender>, <date>, re: <subject>". Without that the model blurs facts
together instead of citing them, and I can't audit where a claim came from.

Deduplicate by email_id. Never include the anchor email's own chunks as
"foreign" — that's just the email restated and it wastes budget.

Test that the budget is actually respected, that priority order holds when the
budget is tight, and that the anchor never appears in the foreign section.
```

### B4 — Rollup briefs

```
Create retrieval/briefs.py:

    def rebuild_dirty(db_path=None, *, limit=None) -> int
    def get_brief(node_type, node_id, db_path=None) -> Optional[Brief]

For each node whose evidence_hash changed, one call on the "brief" stage
(routes to LLM_MODEL_BRIEF) producing headline, body_md, and open_items.
Schema with `reason` declared first, maxLength on every string.

Two gates that matter for cost:
  - Only build briefs for nodes with 2 OR MORE emails. Without this you generate
    160 single-email briefs that say nothing the summary didn't.
  - Skip entirely when evidence_hash is unchanged. That's the whole point of
    storing it.

Brief content is a STATE DOCUMENT, not a digest. Write the prompt so it answers:
what's happened, who's involved, what's still open, what was decided. It should
NOT be a per-email summary list — we already have per-email summaries and
concatenating them is worthless.

Test with a fake client against the B1 fixture. Assert the 2-email gate holds
and that an unchanged hash results in zero model calls.
```

### B5 — Rewire outline and summarize

```
Read drafting/outline.py and summarization/summarize.py in full first.

Add an optional context parameter to both:

    generate_reply_outline(processed, raw, context: Optional[ContextPack] = None)
    summarize(email, context: Optional[ContextPack] = None)

When context is present, insert a "What you already know:" section above the
body in the user message. Use retrieval.pack output directly — do not
re-assemble it.

Extend OUTLINE_SYSTEM_PROMPT (drafting/outline.py:37) with two sentences:
draw on the prior context to make bullets concrete and specific; never invent
facts absent from BOTH the email and the context. Keep every existing sentence
in that prompt — each one is there because of a specific failure.

TWO HARD CONSTRAINTS:
  - Do NOT touch is_eligible at drafting/outline.py:71. The read-status and
    no-reply gate stays exactly as it is.
  - The parameter defaults to None and must preserve current behavior exactly,
    so all 327 lines of drafting/tests/test_outline_gating.py pass UNCHANGED.
    Run that file specifically and show me the result before you say you're done.
```

### B6 — Fix the dead scoring signals

```
Read scoring/signals.py and scoring/score.py.

There's a live bug. scoring/signals.py:20 hardcodes
ACCOUNT_OWNER = "iamsamkitshah@gmail.com" and DEFAULT_VIP_SENDERS is an empty
frozenset. The mailbox has since changed, so is_direct is computed against the
wrong address and is_vip is always False.

Check _WITHIN_BAND_WEIGHTS at scoring/score.py:135 and tell me what fraction of
the scoring weight is currently dead before you fix anything.

Then fix it properly:
  - Derive owner identity from ingestion (the authenticated Gmail profile),
    not a module constant. Look at ingestion/gmail_auth.py for where that's
    available. Cache it; don't hit the API per email.
  - Derive VIP from graph interaction frequency — people above a percentile of
    two-way exchange volume, computed from the entity/mention tables.

If you think either of these is the wrong fix rather than the real one, say so
before implementing.
```

### B7 — Retrieval CLI

```
Create retrieval/cli.py, following pipeline/cli.py's argparse structure.

    python -m retrieval.cli search "<query>"
        ranked results with which channel found each one — I need to see whether
        the graph channel is contributing or whether BM25 is carrying everything

    python -m retrieval.cli pack --email <email_id>
        the literal ContextPack an outline would receive, section by section,
        with the char count of each

    python -m retrieval.cli brief case <entity_id>
        the stored brief

`pack --email` is the tool I'll use most — when an outline comes out generic,
this is how I tell whether the context was empty or whether it was present and
the model ignored it. Make its output readable.
```

---

## 7. 🅒 Person C — The agent and the extension

**Branch:** `track-c-agent` **Owns:** `/agent/`, `/extension/`, `api/main.py`

**Do not wait on Person B.** C1 tells you how to stub their layer.

### C1 — Agent tools

```
Create agent/tools.py:

    TOOL_SPECS: list[dict]          # Anthropic tool-definition format
    def dispatch(name, args, *, db_path=None) -> dict

Nine tools. REUSE the existing implementation for each — do not reimplement:

  search_context(query, k?)          -> retrieval.pack.build_pack
  get_email(email_id)                -> pipeline.persist.get + ingestion.store.get
  get_thread_brief(thread_id)        -> retrieval.briefs.get_brief
  get_entity_brief(entity_id)        -> retrieval.briefs.get_brief
  list_entities(kind?, query?)       -> context.store
  find_open_items(person?, case?)    -> node_brief.open_items
  list_queue(filters)                -> REUSE api/filters.py. Read it first.
                                        Do not write a second filtering path.
  draft_reply(email_id, instructions)-> REUSE drafting/expand.py
  summarize_selection(email_ids)     -> retrieval.pack + one model call

Person B is building retrieval in parallel. I'm not waiting on them: put the
retrieval-backed tools behind a small seam and provide a stub that returns a
hand-written ContextPack fixture, so the whole loop and UI are testable today.
Make the swap to the real build_pack a one-line change.

Every tool result must be JSON-serializable AND BOUNDED — cap list results,
truncate long text. An unbounded get_email on a 5000-char body blows the loop's
context by turn three.

Write clear tool descriptions. The model picks tools based on these strings, and
a vague description is the single most common cause of a bad agent.
```

### C2 — Tool-use loop

```
Create agent/loop.py:

    def run(messages, *, max_turns=8, db_path=None) -> Iterator[Event]

Standard Anthropic tool-use loop: call messages.create with tools=TOOL_SPECS,
while stop_reason == "tool_use" dispatch each tool_use block and append a
tool_result block, repeat until max_turns. Yield events (text_delta, tool_start,
tool_end, done) so the transport layer can stream them.

Get the client via llm.client.get_client("agent").

CRITICAL: raise a clear, explicit error if the resolved provider is ollama.
llm/ollama.py has no `tools` support — it would silently ignore the parameter
and return a confident answer having called nothing. Fail loudly instead. Check
this before the first API call, not after.

System prompt: you are Valence, an assistant over this user's mailbox. Always
ground answers in tool results and cite email_ids. Never claim to have sent
anything. When asked to draft, return the draft for review.

Test with a scripted fake client that returns canned tool_use blocks — no
network. Cover: zero tool calls, one call, a chain of three, and hitting
max_turns.
```

### C3 — Conversation persistence

```
Create agent/conversation.py, persisting to the agent_conversation and
agent_message tables from Checkpoint 0. Use models.db.connect; follow the row
mapping style in pipeline/persist.py.

    create(title=None) -> conversation_id
    append(conversation_id, role, content)
    history(conversation_id, limit=None) -> list[dict]
    recent(limit=20) -> list[Conversation]

This matters more than it looks: the extension panel lives inside Gmail, which
is a SPA that remounts content scripts constantly. In-memory chat state will not
survive a user clicking between messages. Everything has to round-trip through
the DB.
```

### C4 — API endpoints

```
Read api/main.py in full first — there are 12 existing endpoints, match their
conventions exactly (module-level DB_PATH so tests can override, same error
shapes, same serializer boundary).

Add:
    POST /api/agent/chat                -> streaming response
    GET  /api/agent/conversations/{id}

The chat endpoint takes a conversation_id (or creates one), appends the user
message, runs agent.loop.run, streams events out, and persists the assistant
turn when done.

Add tests to api/tests/, following api/tests/test_api.py. Use the fake client
from C2 so the tests need no network.
```

### C5 — Extension streaming transport

```
Read extension/background.js and extension/content/api.js in full first.

The existing message-based proxy (content script -> service worker -> fetch)
exists to bypass Gmail's CSP. Keep it for all non-streaming calls; it works.

But chrome.runtime.sendMessage CANNOT stream. For the agent endpoint, add a port
transport instead: the content script opens chrome.runtime.connect, the service
worker fetches, reads the ReadableStream, and posts chunks over the port as they
arrive.

Why it matters: with up to 8 tool turns a response can take 20+ seconds. A
blocking spinner for 20 seconds reads as broken.

TIMEBOX THIS. MV3 service workers can be hostile about long-lived connections
and stream handling. If you're fighting it after a reasonable attempt, fall back
to a non-streaming POST with a spinner plus a "using tool X..." progress line,
and tell me you took the fallback. Shipping the fallback beats losing the day.
```

### C6 — The "Ask" tab

```
Read extension/content/detail.js in full first — especially the tab structure
and the inbox navigation at lines 288-367.

Add extension/content/ask.js: a third tab, "Ask", beside the existing Email and
Inbox tabs in the panel.

  - message list (user / assistant turns)
  - text input, submit on Enter
  - streaming assistant text via the C5 port transport
  - a collapsed "used N sources" line listing the cited emails; clicking one
    sets location.hash = "#all/" + threadId, matching the existing navigation
    pattern exactly
  - conversation persists across Gmail navigation via the C3 endpoints

ALL text goes through textContent, NEVER innerHTML. See the note at
detail.js:22 — this renders model output inside the user's mail client, so it's
an injection surface.

Follow the existing panel's visual language; read extension/content/styles.css
and DESIGN.md. It should look like it was always there.
```

### C7 — Context chips and the feedback port

```
Two additions to extension/content/detail.js.

FIRST — a "Context" section for the open message: linked case and project chips
plus related emails, from the graph. Clicking a related email navigates the same
way the Inbox tab rows do.
This is what makes the graph VISIBLE rather than merely felt. Without it the
correlation is invisible to the user and they can't tell it's working.

SECOND — port the feedback controls. Read api/static/index.html:578 and
index.html:604 (sendFeedback). Move the segmented importance-level picker and
the automated/real toggle into the extension detail panel, hitting the existing
POST /api/emails/{id}/feedback endpoint. Do not change the endpoint.

This port is REQUIRED, not cosmetic. We're dropping the webapp, and index.html
is currently the ONLY UI for the sender-priors feedback loop shipped in commit
698aba4. Without this port that entire feature becomes unreachable.

Leave api/static/index.html itself untouched — it stays on disk as an
unmaintained debug view.
```

### C8 — Tests

```
Fill out agent/tests/:
  - tool dispatch against a fake DB, including the bounded-output assertions
  - the loop against the scripted fake client from C2 — no network
  - the ollama-provider guard actually raises

Add api/tests/ coverage for the two new endpoints, following the style of
api/tests/test_api.py.

Then run `pytest -q`. All 463 existing tests plus the new ones must pass.
Show me the output.
```

---

## 8. Phase 2 & 3 prompts

### M1 / M2 / M3 — Merges (run by A, then B, then C)

```
I'm merging my branch <branch-name> into main. Before I do:

1. Rebase onto latest main and show me any conflicts. If a conflict is in a file
   my track does not own, STOP — that means someone crossed the ownership line
   and we need to talk, not resolve it silently.
2. Run `pytest -q` on the rebased branch.
3. List every file this branch touches and confirm each one is in my track's
   ownership list from PHASES-COMPLEX.md §1.
4. Summarize what this branch adds in five lines or less.

Do not merge or push. I'll do it once I've read this.
```

### INT1 — Swap stubs for the real thing

```
All three branches are now merged into main. Replace the development stubs with
real wiring:

1. retrieval/ currently reads Person B's fixture DB in some paths. Point
   everything at the real graph tables. Keep the fixtures — they're still the
   test substrate.
2. agent/tools.py has a stub for the retrieval-backed tools. Swap it for the
   real retrieval.pack.build_pack.
3. pipeline/refresh.py::process_incremental needs the two-pass structure wired
   end to end: context pass over changed emails, then context.consolidate, then
   retrieval.briefs.rebuild_dirty, THEN the existing reasoning pass, then the
   existing apply_feedback_priors and apply_score_spread post-passes in that
   order.

The ordering in step 3 is the thing to get right: every email's context must be
extracted and consolidated before ANY email's outline is generated, or early
emails get outlines built on an empty graph.

Run `pytest -q` and show me the full pipeline stage order you ended up with.
```

---

## 9. Verification — run together, in order

```bash
ollama pull nomic-embed-text
pip install -r requirements.txt

# 1. Get the real corpus in — only 2 emails are currently ingested
python -m ingestion.cli ingest --limit 200
python -m ingestion.cli count                  # expect ~160

# 2. Build the graph, then INSPECT IT before trusting anything downstream
python -m pipeline.cli process
python -m context.cli graph
python -m context.cli chunks <some_email_id>   # did quote-stripping work?
```

**Step 2 is the go/no-go.** Expect CASE nodes for the ticket IDs in the test mail, PROJECT nodes grouping them, PERSON nodes with plausible counts. If cases are fragmented — one node per mention — tune `context/resolve.py`'s cosine threshold and normalization **here**. Everything downstream inherits this graph; do not proceed past a bad one.

```bash
# 3. Retrieval quality
python -m retrieval.cli search "<a case described in words, not its ID>"
python -m retrieval.cli pack --email <email_id>
python -m retrieval.cli brief case <entity_id>
```

Expect hits from emails that never use the query's words. That's the whole point — if every hit is a keyword match, the graph and vector channels aren't contributing and the RRF fusion needs checking.

```bash
# 4. The actual payoff
python -m pipeline.cli process --only outline
python -m interface.review_cli --limit 10
```

Compare an outline against its pre-change version for the same email. The new one should reference facts that live in **other** emails. If it doesn't, run `retrieval.cli pack --email` on that message — either the context was empty, or it was present and didn't reach the prompt.

```bash
# 5. Extension
uvicorn api.main:app --reload
```

Reload the unpacked extension, open Gmail, check: case/project chips in the detail panel; the Ask tab answering a cross-thread question with citations; the feedback level picker present and persisting through a refresh.

```bash
# 6. Regression
pytest -q      # all 463 existing tests must still pass
```

---

## 10. Cost and risk

| | |
|---|---|
| One-time extraction | ~160 Claude calls on `claude-sonnet-5`. Opus is overkill for span extraction. |
| Embeddings | Local, free, ~1,500 texts. |
| Briefs | ~30–60 calls initially, then only dirty nodes. |
| Steady state | One extraction call per new email + affected briefs. |

**Primary risk — entity resolution over- or under-merging.** Mitigated by keeping `resolve.py` deterministic and unit-testable, and by making `context.cli graph` (A6) a mandatory gate before anything depends on the graph.

**Secondary risk — MV3 service-worker streaming (C5).** Documented fallback: non-streaming with a spinner. Timeboxed on purpose.

**Third risk — quote-stripping (A1).** If it under-strips, every chunk in a thread embeds near-identically and retrieval degrades to noise. `context.cli chunks <email_id>` on a deeply-nested forward is the check. Do it early, not at integration.
