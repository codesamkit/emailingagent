# AI Email Agent — Phased Build Playbook (for Claude Code)

## How to use this
Feed each phase's prompt to Claude Code **one at a time, in order**, within the owner's track. Don't paste the whole document at once — let Claude Code finish and verify each phase before moving to the next.

Before starting, read:
- **`CONTEXT.md`** — project summary, the 3-track ownership map, the `ProcessedEmail` schema, integration checkpoints, and design rationale.
- **`FILE-TREE.md`** — target directory layout.

This doc only contains the **run order** (who runs which phase, when) and the **actual prompts** to paste into Claude Code for each phase. It assumes Claude Code has repo access and can read `CONTEXT.md`/`FILE-TREE.md` itself — prompts below reference those files instead of re-stating them.

---

## Run order — who runs each phase, and when

| Phase | Runs as | Depends on / runs when |
|---|---|---|
| 0 | **All 3, together** (pair on this — don't parallelize) | First, before any other phase |
| 1 | **Person A** (Track A) | After Phase 0 is frozen |
| 1B | **Person A** (Track A) | After Phase 0; can run right after Phase 1 or in parallel |
| 2 | **Person B** (Track B) | After Phase 0; parallel with Track A |
| 3 | **Person B** (Track B) | After Phase 2 |
| 4 | **Person B** (Track B) | After Phase 3 |
| 5 | **Person C** (Track C) | After Phase 0; parallel with Tracks A/B, develops against fixtures |
| 6 | **Person C** (Track C) | After **Checkpoint 1** (Track A's real data replaces Track B/C fixtures) |
| 7 | **Person C** (Track C) | After Phase 6 |
| 8 | **All 3**, each on their own track | After Phases 1–7 are merged |

Full ownership map, per-track file lists, and the integration checkpoint rules are in `CONTEXT.md` — read that before Phase 0.

---

## Project Context (paste once, at the start of every session, for every person)

```
I'm building an AI email agent — see CONTEXT.md in this repo for the full behavior spec,
schema, and ownership map before doing anything else.

Tech stack: [FILL IN — e.g. Python + FastAPI backend, Gmail API, Google Calendar API,
Claude API for classification/summarization/drafting, SQLite for local dev storage,
React frontend later]

We are working in phases, split across 3 people working in parallel (see CONTEXT.md's
ownership map). Stay inside your owned files/folders only. Do not implement future
phases early. At the end of each phase, run/test what you built and report back before
moving to the next phase in your track.
```

**Fill in the tech stack line above before running Phase 0** — better structural choices come from locking that in early.

---

## Phase 0 — Requirements & Architecture Lock-In
**Runs as: all 3, together.** This is the shared contract everything else depends on — pair on it, don't split it up.

```
Read CONTEXT.md (schema draft, ownership map) and FILE-TREE.md (proposed structure) —
both already contain a draft. Your job this phase is to confirm or refine those drafts,
not invent from scratch:

1. Confirm or adjust the proposed folder structure in FILE-TREE.md against the ownership
   map in CONTEXT.md.
2. Confirm or adjust the ProcessedEmail schema in CONTEXT.md; write the agreed version to
   models/schema.py.
3. Write interfaces/README.md documenting the expected function signature for each module
   boundary (ingestion -> raw_email, classification -> is_no_reply, scoring ->
   importance_score, summarization -> summary, calendar -> calendar_context, drafting ->
   reply_outline) so each person can build against a stub.
4. Propose how importance will be scored (signals to combine).
5. Propose how no-reply will be detected (address patterns + headers + LLM fallback).
6. Propose how scheduling-relevant emails will be detected (to know when to pull calendar
   context at all — e.g. keyword/intent check before bothering with a Calendar API call).
7. Ask any clarifying questions (auth method for Gmail + Calendar, which account, rate
   limits, emails-per-run, read-only vs. eventually sending/creating events).

Do not write implementation code yet beyond models/schema.py and interfaces/README.md.
Once agreed, models/schema.py and interfaces/README.md are frozen — further changes go
through the other two people first (see CONTEXT.md's checkpoint rules).
```

**Confirm before splitting into tracks:** schema, interfaces doc, importance signals, no-reply detection approach, scheduling-detection approach.

---

## TRACK A (Person 1): Ingestion & Calendar

### Phase 1 — Email Ingestion
**Runs as: Person A only.** Owned files: `/ingestion/` (see `CONTEXT.md` for the full ownership map).

```
Implement email ingestion:
1. Set up Gmail API auth (OAuth, token storage/refresh) — read-only scope for now.
2. Fetch the N most recent emails (configurable): sender, subject, body (plain text,
   strip HTML safely), received_at, read/unread status, thread_id, raw headers
   (List-Unsubscribe, Precedence, Auto-Submitted — needed for no-reply detection downstream).
3. Store each raw email in a raw_email table.
4. CLI command to run ingestion manually, print count + sample.
5. Handle pagination and rate-limit backoff.

Test with real/sandbox emails and show the stored output before moving to Phase 1B.
Do not touch any folder outside /ingestion/.
```

### Phase 1B — Google Calendar Integration
**Runs as: Person A only.** Owned files: `/calendar/`.

```
Implement Calendar integration:
1. Set up Google Calendar API auth (can share the OAuth flow with Gmail if scopes allow,
   otherwise a second consent step) — read-only scope for now (freebusy + events.list).
2. Write a function get_calendar_context(around_date_range) that returns free/busy blocks
   and any relevant existing events for a given window (e.g. next 7-14 days, configurable).
3. Write a function suggest_available_slots(duration, date_range, working_hours) that
   proposes 2-3 open slots given the free/busy data.
4. Write a lightweight is_scheduling_related(email) intent check (keyword/pattern based,
   e.g. "are you free", "schedule a call", "reschedule", calendar invite MIME type) that
   Track C will call before deciding whether to pull calendar context for a given email
   — this can live in /calendar/ since it's calendar-adjacent, and Track C imports it.
5. CLI command to test: given a fake scheduling email, print the calendar context + suggested slots.

This module doesn't need real emails yet — test with hardcoded sample email text.
Do not touch any folder outside /calendar/.
```

---

## TRACK B (Person 2): Classification & Intelligence

### Phase 2 — No-Reply Detection
**Runs as: Person B only.** Owned files: `/classification/`.

```
Implement no-reply detection as its own module:
1. Rule-based pass: sender patterns (no-reply@, noreply@, donotreply@, notifications@,
   alerts@), List-Unsubscribe / Auto-Submitted / Precedence: bulk headers.
2. LLM fallback for ambiguous cases using sender + subject + first few lines of body.
3. Output is_no_reply + reason matching the ProcessedEmail schema field.
4. Write tests: obvious no-reply, obvious personal, and 3+ ambiguous cases (newsletter
   with real reply-to, automated alert from a real person's shared inbox, calendar invite).

Develop and test against mock RawEmail rows (don't wait on Track A's real ingestion —
use fixtures). Do not touch any folder outside /classification/.
```

### Phase 3 — Importance Ranking
**Runs as: Person B only.** Owned files: `/scoring/`.

```
Implement importance scoring:
1. Rule-based signals (VIP/contacts list, direct vs. CC, urgency keywords, thread
   recency, unread-and-aging decay) as agreed in Phase 0.
2. LLM call for final importance_score + justification, given the rule-based signals as context.
3. Sort/filter helper: "top N most important" / "all urgent + unread."

Test against mock/fixture data. Do not touch other tracks' folders.
```

### Phase 4 — Summarization
**Runs as: Person B only.** Owned files: `/summarization/`.

```
Implement summarization:
1. 1-3 sentence summary per email: what it's about, what's being asked (if anything),
   any deadline mentioned. Factual only — no inferred action items beyond what's stated/implied.
2. Batch efficiently for volume — don't do one LLM call per email if avoidable, without
   sacrificing accuracy.

Test on a fixture batch including one long and one very short email.
Do not touch other tracks' folders.
```

---

## TRACK C (Person 3): Draft Outlines & Orchestration/UI

### Phase 5 — Reply Outlines (Read + Non-No-Reply Only, Calendar-Aware)
**Runs as: Person C only.** Owned files: `/drafting/`.

```
Implement reply OUTLINE generation — bullet points of what the reply should cover,
NOT a full written-out email:

1. Draft-outline generation ONLY runs when: read_status == "read" AND is_no_reply == false.
   Enforce this as an explicit code-level guard (an if-check) before any LLM call — the LLM
   should never be invoked at all for excluded emails, not just instructed to skip them.
2. Unread emails: reply_outline stays null, reply_outline_status = "none". No LLM call.
3. No-reply emails: reply_outline stays null always, reply_outline_status = "not_applicable" —
   the UI should show "No-Reply — no response needed," never a blank/broken-looking field.
4. For eligible emails: generate reply_outline as a short bullet list, e.g.:
     - Acknowledge the request
     - Confirm you can make Wed 2pm or Thu 10am (per calendar availability)
     - Ask if they need the doc beforehand
   Keep each bullet short — this is a skeleton for the user to expand, not prose.
5. If is_scheduling_related(email) (from Track A's /calendar/ module) returns true, call
   get_calendar_context / suggest_available_slots and fold the suggested slots into the
   outline as a bullet, instead of having the LLM guess availability.
6. Add an on-demand "expand_outline_to_full_draft(email_id)" function for later use (stub
   is fine for now) — so the user can ask for a full email only when they actually want one.
7. Add regeneration: when a previously-unread email's read_status flips to read, the
   outline should regenerate then, not only at ingestion time.

Write a test asserting: unread emails never get an outline, no-reply emails never get
one (even if manually marked read), only read + non-no-reply get one, and scheduling
emails include calendar-derived slot suggestions in the outline. This is correctness-
critical — test it explicitly.

Develop against Track A/B's fixture outputs (mock ProcessedEmail + CalendarContext
objects) so you're not blocked waiting on their real implementations.
Do not touch any folder outside /drafting/.
```

### Phase 6 — Orchestration Pipeline
**Runs as: Person C only.** Owned files: `/pipeline/`. Runs after **Checkpoint 1** (Track A's real ingestion output has replaced Track B/C's fixtures).

```
Build the end-to-end pipeline that imports from all other tracks (import only — don't
edit their internals):
1. Ingest (Track A) -> no-reply detection (Track B) -> importance scoring (Track B) ->
   summarization (Track B) -> calendar context where scheduling-related (Track A) ->
   reply outline where eligible (Track C).
2. Persist to processed_email table.
3. Incremental re-run: when read_status changes for a specific email, only re-run
   summarization (if needed) and outline generation for that email — don't reprocess
   the whole inbox.
4. CLI command process_inbox printing: sender | subject | importance | read? | no-reply? |
   scheduling? | has outline?

This is the first point real integration across all 3 tracks happens — do this as its
own PR touching only /pipeline/.
```

### Phase 7 — Output / Review Interface
**Runs as: Person C only.** Owned files: `/interface/`.

```
Build a simple interface (CLI or minimal web view — propose one) showing:
1. Emails sorted by importance.
2. Summary inline.
3. No-reply flag clearly ("No-Reply — informational only").
4. Reply outline where one exists, editable, with an "expand to full draft" action
   (calls the Phase 5 stub) — no auto-send, human-in-the-loop.
5. Filter by read/unread, importance, no-reply, scheduling-related.

Propose the interface approach before building it.
```

---

## Phase 8 — Hardening & Edge Cases
**Runs as: all 3, each reviewing their own track's owned modules.** Runs after Phases 1–7 are merged.

```
Each person reviews their own owned module for:
1. (Person A) Emails with no body/attachment-only; Calendar API failures/rate limits;
   ambiguous timezone handling in suggested slots.
2. (Person B) Very long threads (summarize the thread, not just latest message); non-English emails.
3. (Person C) Outlines going stale when a thread gets new messages after read_status
   flipped; outline regeneration edge cases.
4. All: basic logging so scores/flags/outlines are debuggable after the fact; graceful
   degradation (e.g. "summary pending" instead of a crash) on API failures.

Report back per-track before merging.
```

---

For the reasoning behind these design choices (why outlines instead of full drafts, why the schema freezes after Phase 0, why gating is code-level not prompt-level, etc.), see the **Design rationale** section of `CONTEXT.md`.
