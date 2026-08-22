# AI Email Agent — Phased Build Prompt (for Claude Code)

## How to use this
Feed each phase to Claude Code **one at a time**, in order, within each person's track (see "Parallelization Plan" below). Don't paste the whole document at once — let Claude Code finish and verify each phase before moving to the next.

This version is designed for **3 people building in parallel** with minimal file/merge conflicts. Read the Parallelization Plan before anyone starts coding — it defines who owns which files and the shared contract everyone codes against.

---

## Project Context (paste once, at the start of every session, for every person)

```
I'm building an AI email agent with the following core behavior:

1. It connects to a user's email inbox (Gmail API to start) AND their Google Calendar.
2. It sorts/ranks emails by importance.
3. It generates a short summary for every email.
4. For emails the user has already READ, it generates a planned reply — as a short OUTLINE
   (bullet points of what the reply should say / key points to hit), NOT a full written-out
   email. The user will expand it into prose themselves or ask for a full draft on demand.
5. For emails that are UNREAD, no reply outline is generated — only classification + summary.
   Outlines get generated once the email is marked read (or on-demand).
6. If an email is from a no-reply address (or is clearly transactional/automated with no
   expectation of a reply), it must be flagged clearly as "No-Reply" and MUST NOT get a
   reply outline at all, regardless of read status.
7. Calendar integration: for emails that involve scheduling (meeting requests, "are you free
   Tuesday", event invites), the agent should check the user's Google Calendar for conflicts/
   availability and use that when generating the reply outline (e.g. "Suggest: propose Wed 2pm
   or Thu 10am — both open on calendar" instead of guessing blind).
8. Output should be inspectable — I want to see the importance score, summary, no-reply flag,
   calendar context (if any), and reply outline (if any) for each email, as structured data
   first, UI later.

Tech stack: [FILL IN — e.g. Python + FastAPI backend, Gmail API, Google Calendar API,
Claude API for classification/summarization/drafting, SQLite for local dev storage,
React frontend later]

We are working in phases, split across 3 people working in parallel (see the shared
Parallelization Plan / file ownership map). Stay inside your owned files/folders only.
Do not implement future phases early. At the end of each phase, run/test what you built
and report back before moving to the next phase in your track.
```

---

## Parallelization Plan — read this before splitting up work

**Principle:** the three people can't fully avoid dependencies (Person B and C both need the data model Person A defines), so we solve that by locking the **data model and interfaces in Phase 0** — as a single shared, small file that everyone treats as read-only after it's agreed. Every other file is owned by exactly one person. Nobody edits another person's owned files. Integration happens at defined checkpoints via PRs, not by editing each other's code directly.

### Shared contract (owned by nobody — agreed once in Phase 0, then frozen)
- `models/schema.py` (or `.ts`) — the `RawEmail`, `ProcessedEmail`, and `CalendarContext` data classes/types.
- `interfaces/README.md` — one page documenting each person's module's expected function signatures (inputs/outputs), so others can write against a stub without waiting on the real implementation.

If someone needs a schema field added after Phase 0 is frozen, they propose it in the interfaces doc and flag it to the other two — don't unilaterally edit the shared schema mid-sprint.

### Track A — Person 1: Ingestion & Calendar
**Owns:** `/ingestion/`, `/calendar/`, `models/schema.py` (initial draft in Phase 0, then frozen)
**Builds:** Phase 0 (schema draft), Phase 1 (Gmail ingestion), Phase 1B (Calendar integration)
**Produces for others:** rows in `raw_email` table + a `CalendarContext` object (free/busy blocks, upcoming events) that Track C consumes.

### Track B — Person 2: Classification & Intelligence
**Owns:** `/classification/` (no-reply detection), `/scoring/` (importance), `/summarization/`
**Builds:** Phase 2 (no-reply detection), Phase 3 (importance ranking), Phase 4 (summarization)
**Depends on:** the frozen schema only (works against mock `RawEmail` rows — doesn't need Track A's real ingestion running to develop and test).
**Produces for others:** `is_no_reply`, `importance_score`, `summary` fields on `ProcessedEmail`.

### Track C — Person 3: Draft Outlines & Orchestration/UI
**Owns:** `/drafting/`, `/pipeline/`, `/interface/`
**Builds:** Phase 5 (outline generation, gated + calendar-aware), Phase 6 (orchestration pipeline), Phase 7 (review UI)
**Depends on:** the frozen schema + mocked outputs from Track A/B (works against fixture data — e.g. a `fixtures.json` of sample processed emails with is_no_reply/importance/summary already filled in — so Track C isn't blocked waiting on the others).

### Merge/integration checkpoints (avoid push conflicts)
1. **Checkpoint 0 (before anyone codes beyond schema):** all 3 agree on `models/schema.py` and `interfaces/README.md` in one sitting (pair on this part, don't parallelize it). Merge this first, tag it, everyone branches from it.
2. Each person works on their own branch (`track-a-ingestion`, `track-b-classification`, `track-c-drafting`) and only touches files inside their owned folders. No one edits `models/schema.py` again without flagging the other two first.
3. **Checkpoint 1:** Track A's real ingestion output gets swapped in for Track B and C's mocks. Do this as a small, separate integration PR — not by any one person editing another's folder.
4. **Checkpoint 2:** Track C's pipeline (Phase 6) wires all three tracks together for the first time end-to-end. This PR touches `/pipeline/` only (Track C's own folder) and imports from the others — it doesn't modify Track A or B's internals.
5. Run CI/tests after every merge to `main`, not just before — catches integration breaks fast since three people are shipping concurrently.

**Rule of thumb to prevent push conflicts:** if a PR would touch a file outside your own folder (other than pulling in a new merged dependency), stop and coordinate first — that's a signal the ownership split needs a quick conversation, not a merge.

---

## Phase 0 — Requirements & Architecture Lock-In (all 3, together)

**Goal:** Get alignment before any code is written. Do this one together, not split up — it's the shared contract everything else depends on.

```
Before writing code, do the following:

1. Propose a project structure (folders/files) matching the ownership map above:
   /ingestion/, /calendar/, /classification/, /scoring/, /summarization/, /drafting/,
   /pipeline/, /interface/, /models/, /interfaces/.
2. Propose models/schema.py with the data model for a "processed email" record, including:
   - email_id, thread_id, sender, subject, received_at, read_status (read/unread)
   - is_no_reply (boolean) + reason
   - importance_score (numeric or enum: low/medium/high/urgent) + short justification
   - summary (1-3 sentences)
   - calendar_context (nullable — populated only when the email is scheduling-related;
     includes relevant free/busy info or suggested time slots)
   - reply_outline (nullable list of bullet points — only populated when
     read_status = read AND is_no_reply = false)
   - reply_outline_status (e.g. none / suggested / edited / expanded_to_draft / sent)
3. Propose interfaces/README.md documenting the expected function signature for each
   module boundary (ingestion -> raw_email, classification -> is_no_reply, scoring ->
   importance_score, summarization -> summary, calendar -> calendar_context, drafting ->
   reply_outline) so each person can build against a stub.
4. Propose how importance will be scored (signals to combine).
5. Propose how no-reply will be detected (address patterns + headers + LLM fallback).
6. Propose how scheduling-relevant emails will be detected (to know when to pull calendar
   context at all — e.g. keyword/intent check before bothering with a Calendar API call).
7. Ask any clarifying questions (auth method for Gmail + Calendar, which account, rate
   limits, emails-per-run, read-only vs. eventually sending/creating events).

Do not write implementation code yet — just the plan, schema, interfaces doc, and questions.
Once agreed, this file is frozen — further changes go through the other two people first.
```

**Confirm before splitting into tracks:** schema, interfaces doc, importance signals, no-reply detection approach, scheduling-detection approach.

---

## TRACK A (Person 1): Ingestion & Calendar

### Phase 1 — Email Ingestion
```
Owned files: /ingestion/ only (plus your initial contribution to models/schema.py in Phase 0).

Implement email ingestion:
1. Set up Gmail API auth (OAuth, token storage/refresh) — read-only scope for now.
2. Fetch the N most recent emails (configurable): sender, subject, body (plain text,
   strip HTML safely), received_at, read/unread status, thread_id, raw headers
   (List-Unsubscribe, Precedence, Auto-Submitted — needed for no-reply detection downstream).
3. Store each raw email in a raw_email table.
4. CLI command to run ingestion manually, print count + sample.
5. Handle pagination and rate-limit backoff.

Test with real/sandbox emails and show the stored output before moving to Phase 1B.
Do not touch /classification/, /scoring/, /summarization/, /drafting/, /pipeline/, or /interface/.
```

### Phase 1B — Google Calendar Integration
```
Owned files: /calendar/ only.

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
Do not touch /ingestion/, /classification/, /scoring/, /summarization/, /drafting/,
/pipeline/, or /interface/.
```

---

## TRACK B (Person 2): Classification & Intelligence

### Phase 2 — No-Reply Detection
```
Owned files: /classification/ only.

Implement no-reply detection as its own module:
1. Rule-based pass: sender patterns (no-reply@, noreply@, donotreply@, notifications@,
   alerts@), List-Unsubscribe / Auto-Submitted / Precedence: bulk headers.
2. LLM fallback for ambiguous cases using sender + subject + first few lines of body.
3. Output is_no_reply + reason matching the ProcessedEmail schema field.
4. Write tests: obvious no-reply, obvious personal, and 3+ ambiguous cases (newsletter
   with real reply-to, automated alert from a real person's shared inbox, calendar invite).

Develop and test against mock RawEmail rows (don't wait on Track A's real ingestion —
use fixtures). Do not touch /ingestion/, /calendar/, /scoring/, /summarization/,
/drafting/, /pipeline/, or /interface/.
```

### Phase 3 — Importance Ranking
```
Owned files: /scoring/ only.

Implement importance scoring:
1. Rule-based signals (VIP/contacts list, direct vs. CC, urgency keywords, thread
   recency, unread-and-aging decay) as agreed in Phase 0.
2. LLM call for final importance_score + justification, given the rule-based signals as context.
3. Sort/filter helper: "top N most important" / "all urgent + unread."

Test against mock/fixture data. Do not touch other tracks' folders.
```

### Phase 4 — Summarization
```
Owned files: /summarization/ only.

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
```
Owned files: /drafting/ only.

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
Do not touch /ingestion/, /calendar/, /classification/, /scoring/, /summarization/.
```

### Phase 6 — Orchestration Pipeline
```
Owned files: /pipeline/ only.

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
own PR touching only /pipeline/, after Checkpoint 1 (Track A's real data replacing
Track B/C's fixtures) is merged.
```

### Phase 7 — Output / Review Interface
```
Owned files: /interface/ only.

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

## Phase 8 — Hardening & Edge Cases (all 3, on their own tracks)

```
Each person reviews their own owned module for:
1. (Track A) Emails with no body/attachment-only; Calendar API failures/rate limits;
   ambiguous timezone handling in suggested slots.
2. (Track B) Very long threads (summarize the thread, not just latest message); non-English emails.
3. (Track C) Outlines going stale when a thread gets new messages after read_status
   flipped; outline regeneration edge cases.
4. All: basic logging so scores/flags/outlines are debuggable after the fact; graceful
   degradation (e.g. "summary pending" instead of a crash) on API failures.

Report back per-track before merging.
```

---

## Notes on the design

- **Why the schema/interfaces file is frozen after Phase 0:** it's the only shared surface between all three people. Treating it as append-only-with-coordination (not silently edited) is what prevents three people from stepping on each other mid-sprint.
- **Why outlines instead of full drafts:** bullet-point outlines are cheaper to generate, faster for the user to scan/approve, and avoid the awkwardness of an AI-voiced paragraph the user has to either send as-is or heavily rewrite. Full-draft expansion is still available on demand (Phase 5, step 6) for when the user actually wants prose.
- **Why calendar-detection is a separate cheap check before the API call:** most emails aren't scheduling-related, so gating the Calendar API call behind a fast keyword/intent check (rather than calling it for every email) keeps latency and API usage down.
- **Why gate outlines on read status at the code level, not just prompt-level:** deterministic code checks don't drift the way prompt instructions occasionally do — important for a hard rule like "never draft for unread/no-reply emails."
- **Fill in your stack specifics** (Gmail/Calendar scopes, DB choice, hosting) in the Project Context block before Phase 0 — better structural choices come from locking that in early.
