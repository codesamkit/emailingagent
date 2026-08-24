# Project Context — AI Email Agent

This file summarizes the phased build plan in `PHASES.md` so future sessions don't need to re-read the full prompt doc for orientation.

## What this project is

An AI email agent that:
1. Connects to a user's Gmail inbox and Google Calendar.
2. Sorts/ranks emails by importance.
3. Generates a short summary for every email.
4. Generates a complete, ready-to-send reply draft for every real email as soon as it arrives — not gated on the user opening it, and not a bullet outline the user expands themselves. (Internally this is still a two-step outline-then-expand LLM chain for quality — see `drafting/`, `pipeline/orchestrate.py`'s `outline`/`expand` stages — but that's an implementation detail; the extension only ever shows the finished draft.)
5. No user choices are asked when drafting (tone, etc.) — the model just writes the best draft it can from the email's content.
6. Flags no-reply/transactional/automated senders as `"No-Reply"` — these **never** get a reply outline or draft. This is the one gate: read status has no bearing on it.
7. For scheduling-related emails, checks Google Calendar for conflicts/availability and folds suggested time slots into the reply outline instead of guessing.
8. Produces inspectable structured output first (importance score, summary, no-reply flag, calendar context, reply outline) — UI comes later.

**Tech stack:** not yet finalized — placeholder in the source doc suggests Python + FastAPI, Gmail API, Google Calendar API, Claude API for classification/summarization/drafting, SQLite for local dev, React frontend later. Confirm/update this before Phase 0 work starts.

## Team structure — 3 parallel tracks

Work is split across 3 people to minimize merge conflicts. Everyone shares one frozen contract from Phase 0; each track otherwise owns its own folders exclusively.

### Shared contract (owned by nobody, frozen after Phase 0)
- `models/schema.py` — `RawEmail`, `ProcessedEmail`, `CalendarContext` data classes.
- `interfaces/README.md` — documents each module's expected function signatures so people can build against stubs.
- Changes after freeze require flagging the other two tracks first — no unilateral edits.

### Track A — Ingestion & Calendar (Person 1)
- Owns: `/ingestion/`, `/calendar/`, initial draft of `models/schema.py`
- Builds: Phase 0 (schema draft) → Phase 1 (Gmail ingestion) → Phase 1B (Calendar integration)
- Produces: `raw_email` rows, `CalendarContext` (free/busy, upcoming events) for Track C.

### Track B — Classification & Intelligence (Person 2)
- Owns: `/classification/`, `/scoring/`, `/summarization/`
- Builds: Phase 2 (no-reply detection) → Phase 3 (importance ranking) → Phase 4 (summarization)
- Depends on: frozen schema only (works against mock `RawEmail` fixtures).
- Produces: `is_no_reply`, `importance_score`, `summary` fields on `ProcessedEmail`.

### Track C — Draft Outlines & Orchestration/UI (Person 3)
- Owns: `/drafting/`, `/pipeline/`, `/interface/`
- Builds: Phase 5 (outline generation) → Phase 6 (orchestration pipeline) → Phase 7 (review UI)
- Depends on: frozen schema + Track A/B fixture outputs (`fixtures.json`).

### Integration checkpoints
1. **Checkpoint 0** — all 3 agree on `models/schema.py` + `interfaces/README.md` together (pair, don't parallelize), merge and tag; everyone branches from it.
2. Each person works on their own branch (`track-a-ingestion`, `track-b-classification`, `track-c-drafting`), touching only their owned folders.
3. **Checkpoint 1** — Track A's real ingestion/calendar output **and** Track B's real classification/scoring/summarization output replace Track C's mocks, as its own small integration PR. Phase 6 needs both tracks' real output, not just Track A's — don't start Phase 6 until Tracks A and B are both merged.
4. **Checkpoint 2** — Track C's Phase 6 pipeline wires all three tracks together end-to-end; PR touches only `/pipeline/` and imports from others.
5. Run CI/tests after every merge to `main`, not just before.
6. Rule of thumb: if a PR would touch a file outside your own folder, stop and coordinate — that's a sign the ownership split needs discussion, not a merge.

## `ProcessedEmail` schema (frozen in Phase 0 — see `models/schema.py`)
- `email_id`, `thread_id`, `sender`, `subject`, `received_at`, `read_status` (read/unread)
- `is_no_reply` (bool, nullable until classified) + `no_reply_reason`
- `importance_score` (float, 0–100, nullable until scored), `importance_level` (low/medium/high/urgent, derived from score via thresholds), `importance_justification`
- `summary` (1–3 sentences, nullable until summarized)
- `is_scheduling_related` (bool, nullable until checked) — cheap keyword/intent gate, set before deciding whether to call the Calendar API at all
- `calendar_context` (nullable — populated only when `is_scheduling_related == true`; free/busy info + suggested slots)
- `reply_outline` (nullable list of bullets — populated whenever `is_no_reply == false` and classification has run; read status has no bearing)
- `reply_outline_status` (`none` / `not_applicable` / `suggested` / `edited` / `expanded_to_draft` / `sent`) — `not_applicable` added to match Phase 5's no-reply gating (was missing from the original draft, which only listed `none`)
- `processed_at` (nullable — set by the pipeline each time this record is (re)processed; supports Phase 6's incremental re-run and Phase 8's debuggability)

Both `importance_score` (numeric) and an `importance_level` category are kept — `scoring/filters.py`'s "top N" needs the numeric score, but "all urgent + unread" (Phase 3) needs a category, so scoring produces both instead of picking one.

## Phase-by-phase plan

| Phase | Track | Description |
|---|---|---|
| 0 | All | Requirements & architecture lock-in: project structure, schema, interfaces doc, importance-scoring approach, no-reply detection approach, scheduling-detection approach. Frozen once agreed. |
| 1 | A | Gmail ingestion: OAuth (read-only), fetch N recent emails (sender/subject/body/received_at/read status/thread_id/headers), store in `raw_email`, CLI to run + inspect, pagination + rate-limit backoff. |
| 1B | A | Calendar integration: OAuth (read-only freebusy/events.list), `get_calendar_context(range)`, `suggest_available_slots(duration, range, working_hours)`, `is_scheduling_related(email)` intent check, CLI test. |
| 2 | B | No-reply detection: rule-based (sender patterns, List-Unsubscribe/Auto-Submitted/Precedence headers) + LLM fallback for ambiguous cases. Tests: obvious no-reply, obvious personal, 3+ ambiguous cases. |
| 3 | B | Importance ranking: rule-based signals (VIP list, direct vs. CC, urgency keywords, thread recency, unread-aging decay) + LLM final score/justification. Sort/filter helper (top N, urgent+unread). |
| 4 | B | Summarization: 1–3 sentence factual summary (topic, ask, deadline). Batched for volume efficiency. |
| 5 | C | Reply outlines: **code-level gate** — only generated when `is_no_reply == false` and classification has run; no-reply → null/`"not_applicable"` always. Read status is deliberately NOT part of the gate — drafting runs on arrival, not on open (revised after initial ship; see `drafting/outline.py`'s docstring). Calendar-aware (folds in suggested slots via Track A's `is_scheduling_related`/calendar functions). `expand_outline_to_full_draft(email_id)` auto-runs as its own pipeline stage right after outline generation, producing a complete draft with no user choices asked. Explicit correctness tests for all the gating rules. |
| 6 | C | Orchestration pipeline: ingest → no-reply → importance → summarize → calendar context (if scheduling) → reply outline (if eligible) → persist to `processed_email`. Incremental re-run on read-status change (not full reprocess). CLI `process_inbox` summary table. |
| 7 | C | Review interface (CLI or minimal web): sorted by importance, inline summary, clear no-reply flag, editable reply outline with "expand to full draft" action (human-in-the-loop, no auto-send), filters by read/unread/importance/no-reply/scheduling. |
| 8 | All | Hardening: Track A — no-body/attachment-only emails, Calendar API failures, timezone edge cases. Track B — long-thread summarization, non-English emails. Track C — stale outlines after new thread messages, regeneration edge cases. All — logging for debuggability, graceful degradation over crashes. |

## Attribution
- The AI assistant never adds itself as an author or co-author (e.g. in commit messages, code comments, or docs) — all work is attributed solely to the human contributors.

## Design rationale (why, not just what)
- **Schema/interfaces frozen after Phase 0**: it's the only shared surface between the three people — freezing it (append-only-with-coordination) prevents them stepping on each other mid-sprint.
- **Outline-then-expand as an internal two-call chain, not a user-facing step**: bullets first, then prose, tends to produce a better-structured draft than one call straight to prose — but the Chrome extension only ever shows the finished draft, not the intermediate bullets, and never makes the user click "expand" or pick a tone. (The `frontend/`/`valence-frontend/` dashboard still exposes the bullets-then-expand flow directly; out of scope for this decision, not the primary shipped surface.)
- **Cheap scheduling-detection gate before the Calendar API call**: most emails aren't scheduling-related, so a fast keyword/intent check avoids paying API/latency cost on every email.
- **Code-level (not just prompt-level) gating for outlines**: deterministic checks don't drift the way prompt instructions can — important for a hard rule like "never draft for a no-reply/automated sender, or before classification has run." (Read status was part of this gate at first ship; removed later so a reply is ready the moment an email arrives rather than when the user opens it — see `drafting/outline.py`.)

## Decisions locked in during Phase 0
- **Tech stack:** Python + FastAPI + SQLite for local dev, Claude API for classification/summarization/drafting, React frontend later.
- **Account:** iamsamkitshah@gmail.com.
- **Emails-per-run default:** 50, configurable via CLI flag/env var.
- **OAuth scope:** write scope requested from Phase 1 onward — `gmail.send` and Calendar `events` (create/update), not just read-only — so the user isn't asked to re-consent later when send/create-event features land. This does **not** change the human-in-the-loop design: Phase 5/7 still never call send/create-event automatically — an explicit user action in the review interface is required every time. The scopes are requested early; the write *code paths* (actually sending, actually creating events) are still future phases, stubbed until then. See PHASES.md Phase 1/1B for the updated scope list.

## File tree

See `FILE-TREE.md` for the proposed project directory structure derived from the Phase 0 ownership map.
