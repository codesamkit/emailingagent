# Project Context — AI Email Agent

This file summarizes the phased build plan in `email-agent-phased-build-prompt.md` so future sessions don't need to re-read the full prompt doc for orientation.

## What this project is

An AI email agent that:
1. Connects to a user's Gmail inbox and Google Calendar.
2. Sorts/ranks emails by importance.
3. Generates a short summary for every email.
4. For emails already marked **read**, generates a planned reply as a short **outline** (bullet points), not a full written email — the user expands it themselves or requests a full draft on demand.
5. For **unread** emails, only classification + summary are generated — no reply outline until the email is read (or outline is requested on-demand).
6. Flags no-reply/transactional/automated senders as `"No-Reply"` — these **never** get a reply outline, regardless of read status.
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
3. **Checkpoint 1** — Track A's real ingestion output replaces Track B/C's mocks, as its own small integration PR.
4. **Checkpoint 2** — Track C's Phase 6 pipeline wires all three tracks together end-to-end; PR touches only `/pipeline/` and imports from others.
5. Run CI/tests after every merge to `main`, not just before.
6. Rule of thumb: if a PR would touch a file outside your own folder, stop and coordinate — that's a sign the ownership split needs discussion, not a merge.

## `ProcessedEmail` schema (proposed in Phase 0)
- `email_id`, `thread_id`, `sender`, `subject`, `received_at`, `read_status` (read/unread)
- `is_no_reply` (bool) + `reason`
- `importance_score` (numeric or low/medium/high/urgent) + short justification
- `summary` (1–3 sentences)
- `calendar_context` (nullable — populated only for scheduling-related emails; free/busy info or suggested slots)
- `reply_outline` (nullable list of bullets — only populated when `read_status == read` AND `is_no_reply == false`)
- `reply_outline_status` (`none` / `suggested` / `edited` / `expanded_to_draft` / `sent`)

## Phase-by-phase plan

| Phase | Track | Description |
|---|---|---|
| 0 | All | Requirements & architecture lock-in: project structure, schema, interfaces doc, importance-scoring approach, no-reply detection approach, scheduling-detection approach. Frozen once agreed. |
| 1 | A | Gmail ingestion: OAuth (read-only), fetch N recent emails (sender/subject/body/received_at/read status/thread_id/headers), store in `raw_email`, CLI to run + inspect, pagination + rate-limit backoff. |
| 1B | A | Calendar integration: OAuth (read-only freebusy/events.list), `get_calendar_context(range)`, `suggest_available_slots(duration, range, working_hours)`, `is_scheduling_related(email)` intent check, CLI test. |
| 2 | B | No-reply detection: rule-based (sender patterns, List-Unsubscribe/Auto-Submitted/Precedence headers) + LLM fallback for ambiguous cases. Tests: obvious no-reply, obvious personal, 3+ ambiguous cases. |
| 3 | B | Importance ranking: rule-based signals (VIP list, direct vs. CC, urgency keywords, thread recency, unread-aging decay) + LLM final score/justification. Sort/filter helper (top N, urgent+unread). |
| 4 | B | Summarization: 1–3 sentence factual summary (topic, ask, deadline). Batched for volume efficiency. |
| 5 | C | Reply outlines: **code-level gate** — only generated when `read_status == read AND is_no_reply == false`; unread → null/`"none"`; no-reply → null/`"not_applicable"` always. Calendar-aware (folds in suggested slots via Track A's `is_scheduling_related`/calendar functions). Stubbed `expand_outline_to_full_draft(email_id)`. Regenerates outline when read_status flips to read. Explicit correctness tests for all the gating rules. |
| 6 | C | Orchestration pipeline: ingest → no-reply → importance → summarize → calendar context (if scheduling) → reply outline (if eligible) → persist to `processed_email`. Incremental re-run on read-status change (not full reprocess). CLI `process_inbox` summary table. |
| 7 | C | Review interface (CLI or minimal web): sorted by importance, inline summary, clear no-reply flag, editable reply outline with "expand to full draft" action (human-in-the-loop, no auto-send), filters by read/unread/importance/no-reply/scheduling. |
| 8 | All | Hardening: Track A — no-body/attachment-only emails, Calendar API failures, timezone edge cases. Track B — long-thread summarization, non-English emails. Track C — stale outlines after new thread messages, regeneration edge cases. All — logging for debuggability, graceful degradation over crashes. |

## Design rationale (why, not just what)
- **Schema/interfaces frozen after Phase 0**: it's the only shared surface between the three people — freezing it (append-only-with-coordination) prevents them stepping on each other mid-sprint.
- **Outlines instead of full drafts**: cheaper to generate, faster to scan/approve, avoids an AI-voiced paragraph the user must send as-is or heavily rewrite. Full-draft expansion is still available on demand.
- **Cheap scheduling-detection gate before the Calendar API call**: most emails aren't scheduling-related, so a fast keyword/intent check avoids paying API/latency cost on every email.
- **Code-level (not just prompt-level) gating for outlines**: deterministic checks don't drift the way prompt instructions can — important for a hard rule like "never draft for unread/no-reply emails."

## Open items to fill in before Phase 0 starts
- Concrete tech stack (backend framework, DB choice, hosting).
- Gmail/Calendar OAuth scopes and which account(s).
- Rate limits and emails-per-run configuration.
- Read-only vs. eventually sending/creating events.

## File tree

See `file-tree.md` for the proposed project directory structure derived from the Phase 0 ownership map.
