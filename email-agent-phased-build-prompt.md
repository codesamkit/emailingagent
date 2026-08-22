# AI Email Agent — Phased Build Prompt (for Claude Code)

## How to use this
Feed each phase to Claude Code **one at a time**, in order. Don't paste the whole document at once — let Claude Code finish and verify each phase before moving to the next. This keeps the agent's context tight and makes debugging much easier. At the start of a new Claude Code session, paste the "Project Context" block below first, then the phase you're working on.

---

## Project Context (paste once, at the start of every session)

```
I'm building an AI email agent with the following core behavior:

1. It connects to a user's email inbox (Gmail API to start).
2. It sorts/ranks emails by importance.
3. It generates a short summary for every email.
4. For emails the user has already READ, it generates a planned/draft reply.
5. For emails that are UNREAD, it does NOT generate a draft reply yet — only classification + summary. Drafts get generated once the user reads the email (or on-demand).
6. If an email is from a no-reply address (or is clearly transactional/automated with no expectation of a reply), it must be flagged clearly as "No-Reply" and MUST NOT get a planned response at all, regardless of read status.
7. Output should be inspectable — I want to see the importance score, summary, no-reply flag, and draft reply (if any) for each email, ideally as structured data first, UI later.

Tech stack: [FILL IN — e.g. Python + FastAPI backend, Gmail API, Claude API for classification/summarization/drafting, SQLite for local dev storage, React frontend later]

We are working in phases. I will give you one phase at a time. Do not implement future phases early. At the end of each phase, run/test what you built and report back before I confirm we move on.
```

---

## Phase 0 — Requirements & Architecture Lock-In

**Goal:** Get alignment before any code is written.

```
Before writing code, do the following:

1. Propose a project structure (folders/files) for this email agent.
2. Propose the data model for a single "processed email" record. It should include at minimum:
   - email_id, thread_id, sender, subject, received_at, read_status (read/unread)
   - is_no_reply (boolean) + reason
   - importance_score (numeric or enum: low/medium/high/urgent) + short justification
   - summary (1-3 sentences)
   - draft_reply (nullable — only populated when read_status = read AND is_no_reply = false)
   - draft_reply_status (e.g. none / suggested / edited / sent)
3. Propose how importance will be scored (rule-based signals like sender, keywords, thread activity, VIP list, deadlines — combined with an LLM judgment call). List the signals you'd use.
4. Propose how "no-reply" will be detected (sender address patterns like no-reply@/donotreply@/notifications@, List-Unsubscribe headers, sender display name heuristics, plus an LLM fallback for ambiguous cases).
5. Ask me any clarifying questions you have before proceeding (auth method, which inbox/account, rate limits, how many emails to process per run, whether this is read-only or will eventually send emails).

Do not write implementation code yet — just the plan, data model, and questions.
```

**You confirm before moving on:** data model, importance signals, no-reply detection approach.

---

## Phase 1 — Email Ingestion

**Goal:** Reliably pull raw emails into your system, nothing smart yet.

```
Implement email ingestion:

1. Set up Gmail API auth (OAuth flow, token storage/refresh) — read-only scope only for now.
2. Write a function to fetch the N most recent emails (configurable) from the inbox, including: sender, subject, body (plain text, strip HTML safely), received_at, read/unread status, thread_id, and raw headers (I need headers for no-reply detection later, e.g. List-Unsubscribe, Precedence, Auto-Submitted).
3. Store each raw email in the local database using a "raw_email" table — don't build the processed/summarized version yet, this phase is just clean ingestion.
4. Write a simple script/CLI command to run ingestion manually and print a count + sample of what was pulled.
5. Handle pagination and basic rate-limit backoff.

Test this by ingesting real (or sandbox) emails and showing me the stored output before moving on.
```

---

## Phase 2 — No-Reply Detection

**Goal:** Isolate this early since it gates Phase 5 behavior. Get it right in isolation.

```
Implement no-reply detection as its own module, independent of importance/summary logic:

1. Rule-based pass first: check sender address against patterns (no-reply@, noreply@, donotreply@, notifications@, alerts@, etc.), check for List-Unsubscribe or Auto-Submitted headers, check Precedence: bulk/auto_reply.
2. For ambiguous cases the rules don't catch, add an LLM fallback that classifies is_no_reply (true/false) with a one-line reason, using sender + subject + first few lines of body.
3. Store is_no_reply + reason on the processed_email record.
4. Write tests covering: obvious no-reply addresses, obvious personal emails, and at least 3 ambiguous cases (e.g. a newsletter with a real reply-to, an automated alert from a real person's shared inbox, a calendar invite).

Show me the classification results on a sample batch before moving on. I want to eyeball false positives/negatives here specifically, since this gates whether a reply gets drafted at all.
```

---

## Phase 3 — Importance Ranking

**Goal:** Score and sort.

```
Implement importance scoring:

1. Build the scoring function combining the signals we agreed on in Phase 0 (e.g. sender in contacts/VIP list, direct address vs. CC, deadline/urgency keywords, thread recency, unread + old = decaying urgency, etc.).
2. Use the LLM to produce a final importance_score + short justification, but pass it the rule-based signals as context so it's not guessing blind.
3. Store importance_score + justification on the processed_email record.
4. Add a sort/filter function so I can pull "top N most important emails" or "all urgent/unread."

Show me a ranked list from a real batch of emails and let me sanity-check the ordering before moving on.
```

---

## Phase 4 — Summarization

**Goal:** One clean summary per email.

```
Implement summarization:

1. For every processed email (regardless of read/unread or no-reply status), generate a 1-3 sentence summary capturing: what it's about, what (if anything) is being asked of the user, and any deadline/date mentioned.
2. Keep summaries factual — no inferred action items unless explicitly stated or clearly implied in the email itself.
3. Store the summary on the processed_email record.
4. Batch this efficiently — don't make one LLM call per email if the volume is high; consider batching multiple short emails into one call where reasonable, but keep accuracy as the priority.

Show me summaries for a sample batch, including at least one long email and one very short one, before moving on.
```

---

## Phase 5 — Planned Replies (Read Emails Only, Non-No-Reply Only)

**Goal:** The core "draft reply" logic — with the gating rules enforced explicitly in code, not just in a prompt.

```
Implement draft reply generation:

1. Draft generation ONLY runs when: read_status == "read" AND is_no_reply == false. Enforce this as an explicit code-level guard (an if-check before the LLM call), not just an instruction inside the prompt — the LLM should never even be invoked for excluded emails.
2. For unread emails: draft_reply stays null, draft_reply_status = "none". Do not call the LLM for a draft at all — this saves cost and avoids drift.
3. For no-reply emails: draft_reply stays null always, draft_reply_status = "not_applicable", and the record should carry a clear is_no_reply flag so the UI can label it "No-Reply — no response needed" instead of leaving it looking broken/empty.
4. For eligible emails (read + not no-reply), generate a draft reply using: the email body, the summary, and the thread history if available (for tone/context continuity). Keep tone matching the user's own prior sent emails if we have access to a sample of them (optional stretch goal — otherwise default to a neutral-professional tone).
5. Add a mechanism to regenerate a draft on-demand (e.g. when a previously-unread email gets marked read later), rather than only running once at ingestion time.

Write a test that asserts: unread emails never get a draft, no-reply emails never get a draft (even if manually marked "read"), and only read + non-no-reply emails get one. This is a correctness-critical rule — test it explicitly, don't just eyeball it.
```

---

## Phase 6 — Orchestration Pipeline

**Goal:** Wire phases 1-5 into one repeatable pipeline.

```
Build the end-to-end pipeline that:

1. Ingests new/updated emails (Phase 1).
2. Runs no-reply detection (Phase 2).
3. Runs importance scoring (Phase 3).
4. Runs summarization (Phase 4).
5. Runs draft generation where eligible (Phase 5).
6. Persists everything to the processed_email table.
7. Re-runs steps 4-5 for a specific email when its read_status changes (don't reprocess the whole inbox every time — make this incremental).

Add a CLI command `process_inbox` that runs the full pipeline on the latest N emails and prints a summary table: sender | subject | importance | read? | no-reply? | has draft?

Run this end-to-end on real data and show me the output table before moving on.
```

---

## Phase 7 — Output / Review Interface

**Goal:** Make it usable, not just correct.

```
Build a simple interface (start with a CLI or a minimal web view — your call, propose one) that lets me:

1. See emails sorted by importance.
2. See the summary inline.
3. See the no-reply flag clearly where applicable ("No-Reply — informational only").
4. See the draft reply where one exists, with the ability to approve/edit/reject it (no auto-send — this stays human-in-the-loop for now).
5. Filter by read/unread, importance level, and no-reply status.

Propose the interface approach before building it.
```

---

## Phase 8 — Hardening & Edge Cases

**Goal:** Don't ship something that breaks on real inboxes.

```
Review and handle:

1. Emails with no body / attachment-only emails.
2. Very long threads (summarize the thread, not just the latest message, when relevant).
3. Non-English emails.
4. Emails that get marked read AFTER a draft was already generated for a prior version of the thread — make sure drafts don't go stale silently.
5. Rate limiting / API failures — retries and graceful degradation (e.g. show the email with "summary pending" rather than crashing the batch).
6. Basic logging so I can see why an email got the importance score / no-reply flag it did, for debugging.

Report back on what you found and fixed.
```

---

## Notes on the design

- **Why gate drafts on read status at the code level, not just prompt-level:** LLMs are good at following instructions most of the time, but "most of the time" isn't good enough for a rule like "never draft for unread/no-reply emails." Putting the check in code before the LLM call makes it deterministic — Claude Code should implement Phase 5 that way rather than trusting a prompt instruction alone.
- **Why no-reply detection is its own early phase:** it's a gating condition for drafting, so getting false negatives here (missing a no-reply email) means drafting a reply nobody will read; false positives mean silently skipping replies the user actually wanted. Worth isolating and testing before it's buried inside the bigger pipeline.
- **Fill in your stack specifics** (Gmail vs Outlook, DB choice, whether this becomes a scheduled job or on-demand) in the Project Context block before Phase 0 — Claude Code will make better structural choices with that locked in early.
