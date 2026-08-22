# Calendar (Track A — Phase 1B)

Reads Google Calendar free/busy and existing events for a date window, proposes
2–3 open meeting slots inside working hours, and provides a cheap
scheduling-intent gate that Track C calls before deciding whether an email is
worth a Calendar API call at all.

**Nothing in this package writes to your calendar.** The write scope is
requested at consent time (see [Scopes](#scopes)) so that event creation in a
later phase does not force a second consent prompt, but no code path here
creates, updates, or deletes an event. That stays gated behind an explicit user
action, per `CONTEXT.md`.

---

## Why `calendaring/` and not `calendar/`

`FILE-TREE.md` originally proposed `calendar/`. A top-level package by that name
shadows the standard library's `calendar` module, and `http.cookiejar` does
`from calendar import timegm` at import time. That breaks `requests`, which
breaks `google-auth`'s transport, which surfaces as:

```
ImportError: The requests library is not installed from please install the
requests package to use the requests transport
```

— a misleading error a long way from its cause. Hence `calendaring`.

---

## 1. One-time Google Cloud setup

Calendar reuses the **same Desktop OAuth client as Gmail**, so if you already
did the Gmail setup in `ingestion/README.md`, you only need step 1 below.

1. **Enable the Google Calendar API** —
   <https://console.cloud.google.com/apis/library/calendar-json.googleapis.com>
   Select the same project you used for Gmail, click **Enable**. Missing this
   step produces a `403 accessNotConfigured` on the first call.

2. If you have *not* done the Gmail setup yet, follow steps 1–5 of
   `ingestion/README.md` first — project, consent screen, test user, Desktop
   OAuth client, client-secrets JSON at the repo root.

3. **Consent:**

   ```bash
   python -m calendaring.cli auth
   ```

   A browser opens. Approve both calendar permissions. The token is written to
   `calendaring/data/calendar_token.json` (gitignored, mode 600).

### Scopes

| Scope | Used by | Used in this phase? |
|---|---|---|
| `calendar.readonly` | `freebusy.query`, `events.list` | **Yes** |
| `calendar.events` | `events.insert` / `events.update` | **No** — requested early, per the Phase 0 decision in `CONTEXT.md` |

Calendar keeps a **separate token file** from Gmail. Google issues one token per
scope set, so a shared file would mean re-consenting to Calendar invalidates
Gmail access, and vice versa.

---

## 2. CLI

Every command except `auth` accepts `--offline`, which substitutes the canned
free/busy data in `samples.py`. You can exercise the whole module before
finishing Google Cloud setup.

```bash
# One-time consent; prints the authorized calendar and its timezone
python -m calendaring.cli auth

# Run the scheduling gate over the built-in sample emails (never touches network)
python -m calendaring.cli intent
python -m calendaring.cli intent --id sched-2

# Free/busy blocks + existing events
python -m calendaring.cli context --days 14
python -m calendaring.cli context --offline

# Proposed open slots
python -m calendaring.cli slots --duration 45 --hours 10-16
python -m calendaring.cli slots --offline --max-slots 2

# Phase 1B acceptance check: fake scheduling email -> context + suggested slots
python -m calendaring.cli demo --offline
python -m calendaring.cli demo --offline --id plain-2   # shows the gate saying no
```

Add `-v` to any command to see retry/backoff and graceful-degradation logging.

---

## 3. Public API

Matches the frozen signatures in `interfaces/README.md`. All the keyword-only
parameters below are **additive** — positional use is exactly as contracted.

```python
from calendaring.scheduling_intent import is_scheduling_related
from calendaring.context import get_calendar_context
from calendaring.suggest import suggest_available_slots

# Cheap gate — no API call, no LLM call. Call this first.
if is_scheduling_related(email):
    ctx = get_calendar_context(range_start, range_end)
    # Pass the context you already have; otherwise this re-fetches it.
    slots = suggest_available_slots(30, working_hours=(9, 17), context=ctx)
```

`suggest_for_context(ctx, 30)` fills `ctx.suggested_slots` in place and returns
it — the one-liner the pipeline wants, since `get_calendar_context` deliberately
leaves that field empty.

`suggest_available_slots` returns `[]` when nothing fits. The caller owns the
fallback wording (e.g. an outline bullet reading "no open slots found this
week").

---

## 4. Behavior worth knowing

- **Working hours are evaluated in the calendar's own timezone**, looked up once
  via `calendars.get` and cached. `9-17` in UTC would put a Los Angeles user's
  suggestions at 2am. Override with `CALENDAR_TIMEZONE`.
- **At most one slot per day.** Three consecutive half-hours on one afternoon is
  technically three options and practically one.
- **Weekends are skipped** unless `--weekends` / `CALENDAR_INCLUDE_WEEKENDS=1`.
- **Start times snap to a 15-minute grid**, so suggestions read as "2:00pm"
  rather than "1:47pm".
- **Nothing is proposed within the next hour** (`CALENDAR_MIN_LEAD_MINUTES`).
- **Busy-ness comes from `freebusy.query`, not from events.** It already handles
  declined invitations, events marked "free", and all-day holds correctly.
- **Recurring events are expanded** (`singleEvents=True`), so a weekly standup
  occupies every one of its slots.
- **Degrades rather than crashes**: an unreadable calendar, an unparseable busy
  period, or a failed timezone lookup logs a warning and continues with what did
  come back.

### The scheduling gate

Tuned to favor **recall over precision**: a false positive costs one extra
`freebusy.query`; a false negative means a scheduling email gets a reply outline
that guesses at availability — the exact failure this project exists to prevent.

A single strong phrase ("are you free", "reschedule", "what time works") or a
real `text/calendar` invite is decisive. Weak signals (a weekday name, a clock
time, the word "meeting") need corroboration. Bulk mail that trips only weak
signals is discounted — see the `NEWSLETTER_WEBINAR` sample, a newsletter
advertising a "Tuesday webinar at 3pm" that a naive keyword match flags and this
one does not.

---

## 5. Configuration

Every value is environment-overridable (`calendaring/config.py`).

| Variable | Default | Meaning |
|---|---|---|
| `CALENDAR_CREDENTIALS_FILE` | shared with Gmail | OAuth client secrets |
| `CALENDAR_TOKEN_FILE` | `calendaring/data/calendar_token.json` | stored token |
| `CALENDAR_WINDOW_DAYS` | `7` | default lookahead |
| `CALENDAR_IDS` | `primary` | comma-separated calendars to consult |
| `CALENDAR_DURATION_MINUTES` | `30` | default meeting length |
| `CALENDAR_WORKING_HOURS` | `9-17` | in the calendar's timezone |
| `CALENDAR_MAX_SLOTS` | `3` | how many slots to propose |
| `CALENDAR_INCLUDE_WEEKENDS` | `false` | allow Sat/Sun |
| `CALENDAR_SLOT_GRANULARITY_MINUTES` | `15` | start-time grid |
| `CALENDAR_MIN_LEAD_MINUTES` | `60` | earliest a slot may start |
| `CALENDAR_BUFFER_MINUTES` | `0` | padding around existing commitments |
| `CALENDAR_TIMEZONE` | calendar's own | IANA name override |
| `CALENDAR_MAX_RETRIES` | `5` | retry budget |

---

## 6. Tests

```bash
python -m pytest calendaring -q
```

151 tests, no network, no consent, no LLM. `tests/fakes.py` scripts the Calendar
resource chain (`service.freebusy().query(body=...).execute()`) so `context.py`
is exercised through its real call path, and `ExplodingService` proves that
passing a pre-built `CalendarContext` makes no API call at all.

---

## 7. Known coupling to flag

- **`calendaring/retry.py` wraps `ingestion.backoff`.** That module is already a
  general Google API retry policy despite its package; reusing it keeps one
  policy in the repo instead of two that drift. If ingestion is ever moved,
  this import moves with it.
- **`calendar_auth.py` duplicates the shape of `ingestion/gmail_auth.py`.** The
  right fix is one shared `google_auth` helper parameterized by scopes and token
  path — but that means editing `/ingestion/`, which was out of scope for this
  phase. `calendar_auth.py` is already written scope-agnostic so the refactor is
  a lift-and-delete when someone owns both folders in one change.
- **`is_scheduling_related` is duck-typed** across two `RawEmail` shapes:
  `models/schema.py` calls the body field `body`, `ingestion/models.py` calls it
  `body_text`. That divergence is a real integration bug living in
  `/ingestion/`, not here; the duck-typing is a deliberate shim until it is
  reconciled at Checkpoint 1.
