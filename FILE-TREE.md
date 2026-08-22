# Proposed File Tree — AI Email Agent

Derived from the ownership map and Phase 0 instructions in `PHASES.md`. Nothing below exists yet — this is the target structure to propose/confirm during Phase 0, not a reflection of current repo contents.

```
aiagent/
├── CLAUDE.md
├── CONTEXT.md
├── FILE-TREE.md
├── PHASES.md
│
├── models/                        # shared contract — frozen after Phase 0
│   ├── schema.py                  # RawEmail, ProcessedEmail, CalendarContext
│   └── db.py                      # shared SQLite connection + table DDL (raw_email, processed_email) —
│                                   # avoids ingestion/store.py and pipeline/persist.py each hand-rolling
│                                   # their own connection/schema setup (DRY, per CLAUDE.md)
│
├── interfaces/                    # shared contract — frozen after Phase 0
│   └── README.md                  # per-module function signatures (inputs/outputs)
│
├── ingestion/                     # Track A — Person 1 (Phase 1)
│   ├── gmail_auth.py              # OAuth flow, token storage/refresh
│   ├── fetch.py                   # fetch N recent emails, pagination, rate-limit backoff
│   ├── store.py                   # raw_email persistence, using models/db.py
│   ├── cli.py                     # manual run command, prints count + sample
│   └── tests/
│       └── test_fetch.py          # pagination, rate-limit backoff, header extraction
│
├── calendaring/                   # Track A — Person 1 (Phase 1B)
│   │                              # NOT `calendar/` — that name shadows the stdlib
│   │                              # `calendar` module and breaks requests/google-auth
│   ├── config.py                  # scopes, token path, window + working-hour defaults
│   ├── calendar_auth.py           # OAuth (freebusy + events.list read; events write
│   │                              # scope requested early, no write code path)
│   ├── retry.py                   # thin adapter over ingestion/backoff.py (one retry
│   │                              # policy in the repo, not two)
│   ├── timeutils.py               # RFC-3339 parsing, interval merge/subtract, tz-aware
│   │                              # working-hour windows — pure, no I/O
│   ├── context.py                 # get_calendar_context(range)
│   ├── suggest.py                 # suggest_available_slots(duration, range, working_hours)
│   ├── scheduling_intent.py       # is_scheduling_related(email)
│   ├── samples.py                 # hardcoded sample emails + canned free/busy,
│   │                              # shared by the CLI's --offline mode and the tests
│   ├── cli.py                     # auth / intent / context / slots / demo
│   ├── README.md
│   └── tests/
│       ├── fakes.py               # scripted Calendar service double — no network
│       ├── test_scheduling_intent.py  # keyword/intent cases for is_scheduling_related
│       ├── test_timeutils.py      # interval math, tz correctness
│       ├── test_context.py        # freebusy/events parsing, pagination, degradation
│       ├── test_suggest.py        # slot fitting, working hours, weekends, lead time
│       └── test_cli.py            # every command, offline
│
├── classification/                # Track B — Person 2 (Phase 2)
│   ├── rules.py                   # sender-pattern + header-based no-reply rules
│   ├── llm_fallback.py            # LLM fallback for ambiguous cases
│   ├── classify.py                # is_no_reply + reason -> ProcessedEmail field
│   └── tests/
│       └── test_classification.py # obvious no-reply / personal / ambiguous cases
│
├── scoring/                       # Track B — Person 2 (Phase 3)
│   ├── signals.py                 # VIP list, direct/CC, urgency keywords, thread recency, decay
│   ├── score.py                   # LLM call -> importance_score + justification
│   ├── filters.py                 # top-N / urgent+unread helpers
│   └── tests/
│       └── test_scoring.py
│
├── summarization/                 # Track B — Person 2 (Phase 4)
│   ├── summarize.py                # 1-3 sentence factual summary
│   ├── batch.py                    # batched LLM calls for volume
│   └── tests/
│       └── test_summarization.py   # long email + very short email fixtures
│
├── drafting/                      # Track C — Person 3 (Phase 5)
│   ├── outline.py                  # reply_outline generation, code-level read/no-reply gate
│   ├── calendar_aware.py           # folds suggest_available_slots into outline bullets
│   ├── expand.py                   # expand_outline_to_full_draft(email_id) — stub
│   └── tests/
│       └── test_outline_gating.py  # unread/no-reply/read+eligible/scheduling assertions
│
├── pipeline/                       # Track C — Person 3 (Phase 6)
│   ├── orchestrate.py               # ingest -> classify -> score -> summarize -> calendar -> outline
│   ├── incremental.py               # re-run only affected steps on read_status change
│   ├── persist.py                   # processed_email persistence, using models/db.py
│   ├── cli.py                       # process_inbox command (sender|subject|importance|...)
│   └── tests/
│       └── test_incremental.py      # re-run only touches the changed email, not the whole inbox
│
├── interface/                      # Track C — Person 3 (Phase 7)
│   ├── review_cli.py                # or a minimal web view — proposed before building
│   └── filters.py                   # read/unread, importance, no-reply, scheduling filters
│
└── fixtures/                        # shared test/dev fixtures (mock RawEmail / ProcessedEmail data)
    └── fixtures.json
```

## Ownership quick reference
- **Track A (Person 1):** `/ingestion/`, `/calendaring/`, initial `models/schema.py` draft
- **Track B (Person 2):** `/classification/`, `/scoring/`, `/summarization/`
- **Track C (Person 3):** `/drafting/`, `/pipeline/`, `/interface/`
- **Shared, frozen after Phase 0:** `/models/`, `/interfaces/`

## Adjustments made this phase
- Added `models/db.py` to the shared contract: both `ingestion/store.py` (Track A) and
  `pipeline/persist.py` (Track C) need SQLite connection setup + table DDL for `raw_email`
  and `processed_email`. Without a shared module, both tracks would independently write
  near-identical connection/schema code — a DRY violation per CLAUDE.md's development
  practices. This is a small addition to the frozen contract; flag before further changes.
- Added `tests/` folders to `ingestion/`, `calendaring/`, and `pipeline/` for consistency with
  `classification/`, `scoring/`, `summarization/`, and `drafting/`, which already had them.

## Adjustments made in Phase 1B
- **`calendar/` renamed to `calendaring/`.** A top-level package named `calendar`
  shadows the standard library's `calendar` module. `http.cookiejar` does
  `from calendar import timegm` at import time, so the shadow breaks `requests`,
  which breaks `google-auth`'s transport — surfacing as a misleading
  "The requests library is not installed". Reproduced against this repo's venv
  before renaming. `interfaces/README.md` was updated to match.
- Added `config.py`, `retry.py`, `timeutils.py`, and `samples.py` to the calendar
  module. `retry.py` deliberately wraps `ingestion/backoff.py` rather than
  reimplementing a second retry policy, and `samples.py` is shared by the CLI's
  offline mode and the test suite instead of each defining its own fixtures
  (DRY, per CLAUDE.md).
- `timeutils.py` exists so the parts most likely to harbor bugs — RFC-3339
  offsets, all-day dates, overlapping busy blocks, timezone-correct working
  hours — are pure functions testable without any API client.
