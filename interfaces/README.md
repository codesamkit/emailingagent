# Module Interfaces — Shared Contract

Each track builds against these signatures using `fixtures/fixtures.json`
mock data before real upstream implementations exist. Do not change a
signature here without flagging the other two tracks first.

## Ingestion (Track A) → raw_email
    fetch_recent_emails(
        max_results: int,
        page_token: str | None = None,
    ) -> tuple[list[RawEmail], str | None]
    # returns (emails, next_page_token)

## Calendar (Track A) → calendar_context / scheduling gate
    is_scheduling_related(email: RawEmail) -> bool
    # cheap keyword/pattern check — no API call, no LLM call

    get_calendar_context(
        range_start: datetime,
        range_end: datetime,
    ) -> CalendarContext

    suggest_available_slots(
        duration_minutes: int,
        range_start: datetime,
        range_end: datetime,
        working_hours: tuple[int, int],  # e.g. (9, 18)
    ) -> list[CalendarSlot]

## Classification (Track B) → is_no_reply
    classify_no_reply(email: RawEmail) -> tuple[bool, str]
    # returns (is_no_reply, reason)

## Scoring (Track B) → importance_score
    score_importance(
        email: RawEmail,
        signals: dict,  # output of signals.py rule pass
    ) -> tuple[ImportanceLevel, str]
    # returns (importance_score, justification)

## Summarization (Track B) → summary
    summarize(email: RawEmail) -> str
    summarize_batch(emails: list[RawEmail]) -> dict[str, str]
    # returns {email_id: summary}

## Drafting (Track C) → reply_outline
    generate_reply_outline(
        processed: ProcessedEmail,
        calendar_context: CalendarContext | None,
    ) -> list[str]
    # caller (pipeline) is responsible for the read/no-reply gate —
    # this function assumes it has already been called only for eligible emails

    expand_outline_to_full_draft(email_id: str) -> str  # stub for Phase 5
