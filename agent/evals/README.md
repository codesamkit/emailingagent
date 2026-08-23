# Ask-tab evals

35 prompts a real person would actually type into Valence's Ask tab, each with
ground truth taken from the StrideCore simulation corpus (160 emails, delivered
to the test inbox 2026-08-23).

The point is not "does it respond." It's whether the response is *good* — and
these are written so that a plausible-sounding wrong answer scores 0 rather than
sliding through.

## Run it

```bash
# backend up first:
#   LLM_PROVIDER=... uvicorn api.main:app --port 8000

python -m agent.evals.run_evals                 # all 35
python -m agent.evals.run_evals --skip-blocked  # only the 23 that work today
python -m agent.evals.run_evals --tier trap     # one tier
python -m agent.evals.run_evals --id T5-01      # one eval
python -m agent.evals.run_evals --render-only   # blank sheet, no backend needed
```

Writes `eval-run.md`: every prompt, the answer it actually gave, which tools it
called vs. which it should have, and a checklist to grade against. Score each
0–3 and the sheet becomes the record.

Each eval opens its own conversation, so one bad answer can't poison the next.
Stdlib only — no new dependencies.

Config: `VALENCE_API` (default `http://127.0.0.1:8000`), `VALENCE_TOKEN` (omit
if auth is disabled), `VALENCE_EVAL_TIMEOUT` (default 180s).

## The tiers

| Tier | n | What it probes |
| --- | --- | --- |
| `triage` | 4 | Can it rank at all, and does it lead with the right thing |
| `rollup` | 5 | Multi-thread status synthesis — "name all the projects and where they stand" |
| `recall` | 6 | Do specific facts and numbers survive summarization |
| `obligations` | 4 | What the user owes, to whom, and by when |
| `synthesis` | 4 | Inference nobody in the corpus states out loud |
| `scheduling` | 2 | Meetings and availability recovered from mail, not calendar |
| `drafting` | 2 | Reply generation in the right register |
| `trap` | 6 | False premises, spam framed as legitimate, unanswerable questions |
| `consistency` | 2 | Same fact asked twice — do the answers match |

## 12 of these will fail right now, and that's the useful part

Five of the nine tools in `agent/tools.py` are still fixture stubs:
`search_context`, `get_thread_brief`, `get_entity_brief`, `list_entities`,
`find_open_items`. Anything needing entity rollups or open-item tracking is
answering from `agent/fixtures.py`, not from real mail.

Those evals are tagged `"blocked_on_stub": true` and flagged in the sheet.
A failure there is a wiring gap, not a model problem — don't tune prompts
against them. `--skip-blocked` gives you the 23 that exercise real paths today.

When Track A/B land, drop the flag and the same evals become the acceptance test
for that work.

## Three evals worth watching closely

**T4-03 — "What's due before September 2nd?"** All 160 emails have the same
`received_at`, because the Gmail connector can't backdate. Every real date lives
in a message body. If the agent reasons from headers this returns nothing, and
that tells you your date extraction isn't running.

**T5-01 — "Are we treating anything as separate problems that are actually the
same problem?"** Two patterns are planted. The sensor one is stated explicitly
by a person in the mail, so finding it scores 2. The unowned-firmware one —
the charging dock and the Hongli test fixture, surfaced in unrelated threads the
same week — is never stated by anyone. Both is a 3.

**T8-06 — the DocuSign phish.** The mailbox contains a genuine disputed vendor
invoice *and* a phishing email pretending to be an invoice. Confusing them is
the failure. This is the one to run after any retrieval change.

## Adding evals

Append to `ask_evals.json`. Required: `id`, `tier`, `prompt`, `tests`,
`must_include`, `fail_signals`. Optional: `should_cite`, `expected_tools`,
`expect_refusal`, `notes`, `blocked_on_stub`.

Write `fail_signals` from the specific wrong answer you expect, not from generic
failure language — "names Kaiho as at-risk, when Kaiho's exposure is zero" is
gradeable; "gives an inaccurate answer" isn't.

Ground truth for anything you add is in `../../CORPUS-WORLD.md` and
`../../meeting-notes/`.
