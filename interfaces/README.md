# Module Interfaces — frozen after Phase 0

Function signatures each track builds against. Owned by nobody; changes require
flagging the other two tracks first (see `CONTEXT.md`'s checkpoint rules).

Everyone can build against these signatures with stub/fixture implementations
before the real ones land — that's the point of freezing this early.

All types referenced below (`RawEmail`, `ProcessedEmail`, `CalendarContext`,
`CalendarSlot`, enums) are defined in `models/schema.py`.

---

## Ingestion (Track A) → produces `RawEmail`

```python
# ingestion/fetch.py
def fetch_recent_emails(
    max_results: int = 50,
    page_token: str | None = None,
) -> tuple[list[RawEmail], str | None]:
    """Fetch up to max_results recent Gmail messages, oldest-first pagination
    via page_token. Returns (emails, next_page_token); next_page_token is
    None when there are no more pages. Raises on auth failure; retries with
    backoff internally on rate-limit (429) responses."""

# ingestion/store.py
def store_raw_emails(emails: list[RawEmail]) -> None:
    """Upsert into the raw_email table (models/db.py), keyed by email_id."""
```

## Classification (Track B) → produces `is_no_reply`

```python
# classification/classify.py
def is_no_reply(email: RawEmail) -> tuple[bool, str]:
    """Returns (is_no_reply, reason). reason is a short human-readable
    string either way (e.g. "sender matches no-reply@ pattern" or
    "personal sender, no bulk headers"). Runs the rule-based pass first;
    falls back to an LLM call only when rules are inconclusive."""
```

## Scoring (Track B) → produces `importance_score`

```python
# scoring/score.py
def score_importance(
    email: RawEmail,
    is_no_reply: bool,
) -> tuple[float, ImportanceLevel, str]:
    """Returns (importance_score 0-100, importance_level, justification).
    Computes rule-based signals internally (scoring/signals.py) and passes
    them as context to a single LLM call for the final score+justification."""

# scoring/filters.py
def top_n(emails: list[ProcessedEmail], n: int) -> list[ProcessedEmail]: ...
def urgent_and_unread(emails: list[ProcessedEmail]) -> list[ProcessedEmail]: ...
```

## Summarization (Track B) → produces `summary`

```python
# summarization/summarize.py
def summarize(email: RawEmail) -> str:
    """1-3 sentence factual summary: topic, ask (if any), deadline (if any)."""

# summarization/batch.py
def summarize_batch(emails: list[RawEmail]) -> dict[str, str]:
    """Returns {email_id: summary}. Batches underlying LLM calls for volume;
    same output contract as calling summarize() on each email individually."""
```

## Calendar (Track A) → produces `calendar_context`

```python
# calendar/scheduling_intent.py
def is_scheduling_related(email: RawEmail) -> bool:
    """Cheap keyword/intent check — no Calendar API call. Gate that decides
    whether get_calendar_context/suggest_available_slots are worth calling
    at all for this email. Imported directly by Track C's drafting module."""

# calendar/context.py
def get_calendar_context(
    range_start: datetime,
    range_end: datetime,
) -> CalendarContext:
    """Free/busy blocks + existing events for the given window. Does not
    set suggested_slots — that's suggest_available_slots' job."""

# calendar/suggest.py
def suggest_available_slots(
    duration_minutes: int,
    range_start: datetime,
    range_end: datetime,
    working_hours: tuple[int, int] = (9, 17),
) -> list[CalendarSlot]:
    """2-3 proposed open slots within working_hours, given free/busy data
    for the range. Returns [] if nothing fits — caller decides fallback
    messaging (e.g. outline bullet says "no open slots found this week")."""
```

## Drafting (Track C) → produces `reply_outline`

```python
# drafting/outline.py
def generate_reply_outline(
    processed: ProcessedEmail,
    raw: RawEmail,
) -> tuple[list[str] | None, ReplyOutlineStatus]:
    """Code-level gate BEFORE any LLM call:
      - read_status != READ            -> (None, ReplyOutlineStatus.NONE)
      - is_no_reply == True            -> (None, ReplyOutlineStatus.NOT_APPLICABLE)
      - else                           -> generate outline bullets,
                                           (bullets, ReplyOutlineStatus.SUGGESTED)
    When processed.is_scheduling_related is True, calls
    calendar.suggest.suggest_available_slots (via processed.calendar_context,
    already populated by the pipeline) and folds the slots into a bullet
    instead of letting the LLM guess availability."""

# drafting/expand.py
def expand_outline_to_full_draft(email_id: str) -> str:
    """Stub for Phase 5; real implementation is a later phase. Takes a
    reply_outline and expands it into full prose the user can send as-is
    or edit further."""
```

---

## Notes on gating (applies to Drafting)

`generate_reply_outline` must check `read_status` and `is_no_reply` with a
plain `if` before touching any LLM client — not via a prompt instruction
telling the model to skip ineligible emails. This is a hard correctness
requirement (see `CONTEXT.md`'s design rationale) and Phase 5's tests must
assert it directly (e.g. by asserting the LLM client is never invoked for
unread/no-reply fixtures, not just asserting the output is null).
