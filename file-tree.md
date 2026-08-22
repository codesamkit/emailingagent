# Proposed File Tree — AI Email Agent

Derived from the ownership map and Phase 0 instructions in `email-agent-phased-build-prompt.md`. Nothing below exists yet — this is the target structure to propose/confirm during Phase 0, not a reflection of current repo contents.

```
aiagent/
├── CLAUDE.md
├── context.md
├── file-tree.md
├── email-agent-phased-build-prompt.md
│
├── models/                        # shared contract — frozen after Phase 0
│   └── schema.py                  # RawEmail, ProcessedEmail, CalendarContext
│
├── interfaces/                    # shared contract — frozen after Phase 0
│   └── README.md                  # per-module function signatures (inputs/outputs)
│
├── ingestion/                     # Track A — Person 1 (Phase 1)
│   ├── gmail_auth.py              # OAuth flow, token storage/refresh
│   ├── fetch.py                   # fetch N recent emails, pagination, rate-limit backoff
│   ├── store.py                   # raw_email table persistence
│   └── cli.py                     # manual run command, prints count + sample
│
├── calendar/                      # Track A — Person 1 (Phase 1B)
│   ├── calendar_auth.py           # OAuth (freebusy + events.list, read-only)
│   ├── context.py                 # get_calendar_context(range)
│   ├── suggest.py                 # suggest_available_slots(duration, range, working_hours)
│   ├── scheduling_intent.py       # is_scheduling_related(email)
│   └── cli.py                     # test command against a fake scheduling email
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
│   ├── persist.py                   # processed_email table writes
│   └── cli.py                       # process_inbox command (sender|subject|importance|...)
│
├── interface/                      # Track C — Person 3 (Phase 7)
│   ├── review_cli.py                # or a minimal web view — proposed before building
│   └── filters.py                   # read/unread, importance, no-reply, scheduling filters
│
└── fixtures/                        # shared test/dev fixtures (mock RawEmail / ProcessedEmail data)
    └── fixtures.json
```

## Ownership quick reference
- **Track A (Person 1):** `/ingestion/`, `/calendar/`, initial `models/schema.py` draft
- **Track B (Person 2):** `/classification/`, `/scoring/`, `/summarization/`
- **Track C (Person 3):** `/drafting/`, `/pipeline/`, `/interface/`
- **Shared, frozen after Phase 0:** `/models/`, `/interfaces/`
