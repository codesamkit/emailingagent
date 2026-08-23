# Ask-tab evals

50 prompts a real person would actually type into Valence's Ask tab, each with
ground truth taken from the StrideCore simulation corpus (225 emails across two
acts, delivered to the test inbox).

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
| `rollup` | 6 | Multi-thread status synthesis — "name all the projects and where they stand" |
| `recall` | 7 | Do specific facts and numbers survive summarization |
| `obligations` | 6 | What the user owes, to whom, and by when |
| `synthesis` | 7 | Inference nobody in the corpus states out loud |
| `scheduling` | 2 | Meetings and availability recovered from mail, not calendar |
| `drafting` | 3 | Reply generation in the right register |
| `trap` | 9 | False premises, spam framed as legitimate, unanswerable questions |
| `consistency` | 3 | Same fact asked twice — do the answers match |
| `temporal` | 3 | Ordering and supersession across the two acts |

## 19 of these will fail right now, and that's the useful part

Five of the nine tools in `agent/tools.py` are still fixture stubs:
`search_context`, `get_thread_brief`, `get_entity_brief`, `list_entities`,
`find_open_items`. Anything needing entity rollups or open-item tracking is
answering from `agent/fixtures.py`, not from real mail.

Those evals are tagged `"blocked_on_stub": true` and flagged in the sheet.
A failure there is a wiring gap, not a model problem — don't tune prompts
against them. `--skip-blocked` gives you the 31 that exercise real paths today.

When Track A/B land, drop the flag and the same evals become the acceptance test
for that work.

## Act 2 adds the temporal tier, which is the sharpest test in the set

All 225 emails share one `received_at`. Act 2's fictional dates are three to
six weeks after Act 1's. Nothing in the headers distinguishes them.

So **TA-01 ("what's changed in the last couple of weeks?") and TA-02 ("what's
the firmware freeze date?") cannot be passed by header reasoning at all.** TA-02
is the meaner one: the freeze date is stated three different ways across the
corpus, and relevance-weighted retrieval will confidently return the superseded
August answer with no hedge.

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
