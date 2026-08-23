"""Manual calendar CLI.

    python -m calendaring.cli auth                     # one-time OAuth consent
    python -m calendaring.cli intent                   # run the gate over the samples
    python -m calendaring.cli context --days 7         # free/busy + events
    python -m calendaring.cli slots --duration 30      # proposed open slots
    python -m calendaring.cli demo                     # fake email -> context + slots
    python -m calendaring.cli demo --offline           # ...with no network or consent
    python -m calendaring.cli create --summary "Test" --start 2026-08-25T14:00:00 --end 2026-08-25T14:30:00
    python -m calendaring.cli update EVENT_ID --summary "Renamed"
    python -m calendaring.cli cancel EVENT_ID

`--offline` swaps in the canned free/busy data from `calendaring/samples.py`,
so every read-only command can be exercised before Google Cloud setup is
finished. `create`/`update`/`cancel` write to your real calendar and have no
offline mode — a write has to prove itself against the real API.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
import warnings
from typing import List, Optional, Sequence

# The Google client libraries emit end-of-life FutureWarnings on Python 3.9 and
# urllib3 warns about macOS's LibreSSL. Neither is actionable from here and both
# bury the CLI's actual output, so they are silenced at the entry point only.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google.*")
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")

from datetime import datetime

from models.schema import CalendarContext, CalendarSlot, ProposedEvent, RawEmail

from . import config, samples
from .calendar_auth import MissingCredentialsError, get_calendar_service
from .context import get_calendar_context, get_calendar_timezone
from .events import create_event, delete_event, update_event
from .scheduling_intent import scheduling_signals
from .suggest import suggest_available_slots
from .timeutils import default_range, ensure_aware, get_timezone


# --- helpers ---------------------------------------------------------------

def _hours(value: str):
    try:
        start, _, end = value.partition("-")
        parsed = (int(start), int(end))
    except ValueError:
        raise argparse.ArgumentTypeError("working hours must look like '9-17'")
    if not 0 <= parsed[0] < parsed[1] <= 24:
        raise argparse.ArgumentTypeError("working hours must satisfy 0 <= start < end <= 24")
    return parsed


def _parse_dt(value: str) -> datetime:
    try:
        return ensure_aware(datetime.fromisoformat(value))
    except ValueError:
        raise argparse.ArgumentTypeError(
            "expected an ISO 8601 datetime like 2026-08-25T14:00:00, got {0!r}".format(value)
        )


def _print_created_event(event: ProposedEvent, tz) -> None:
    """`create_event` never raises — a failure lands here with `error` set."""
    if event.error:
        print("Failed   : {0}".format(event.error))
        return
    print("Event id : {0}".format(event.google_event_id))
    print("Title    : {0}".format(event.title))
    print("When     : {0}".format(_fmt_slot(CalendarSlot(event.start, event.end), tz)))
    if event.location:
        print("Location : {0}".format(event.location))
    if event.attendees:
        print("Attendees: {0}".format(", ".join(event.attendees)))


def _print_raw_event(raw: dict) -> None:
    """`update_event` returns the Calendar API's own JSON shape, not the
    normalized ProposedEvent — different enough from _print_created_event's
    input that reusing it would just be confusing."""
    print("Event id : {0}".format(raw.get("id")))
    print("Summary  : {0}".format(raw.get("summary")))
    start = (raw.get("start") or {}).get("dateTime")
    end = (raw.get("end") or {}).get("dateTime")
    if start or end:
        print("When     : {0} -> {1}".format(start, end))
    if raw.get("location"):
        print("Location : {0}".format(raw["location"]))
    if raw.get("htmlLink"):
        print("Link     : {0}".format(raw["htmlLink"]))


def _truncate(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt(dt, tz) -> str:
    if dt is None:
        return "?"
    return dt.astimezone(tz).strftime("%a %d %b  %H:%M")


def _fmt_slot(slot: CalendarSlot, tz) -> str:
    start = slot.start.astimezone(tz)
    end = slot.end.astimezone(tz)
    same_day = start.date() == end.date()
    return "{0} - {1}".format(
        start.strftime("%a %d %b  %H:%M"),
        end.strftime("%H:%M" if same_day else "%a %d %b  %H:%M"),
    )


def _offline_context(days: Optional[int]) -> CalendarContext:
    """A CalendarContext built from canned data — no network, no consent."""
    range_start, range_end = default_range(days)
    return CalendarContext(
        range_start=range_start,
        range_end=range_end,
        busy_blocks=samples.demo_busy_blocks(range_start),
        existing_events=samples.demo_events(range_start),
        suggested_slots=[],
    )


def _load_context(args: argparse.Namespace) -> CalendarContext:
    if args.offline:
        return _offline_context(args.days)
    service = get_calendar_service(allow_interactive=not args.non_interactive)
    range_start, range_end = default_range(args.days)
    return get_calendar_context(range_start, range_end, service=service)


def _resolve_timezone(args: argparse.Namespace) -> str:
    if args.offline:
        return config.TIMEZONE or "UTC"
    return get_calendar_timezone(
        get_calendar_service(allow_interactive=not args.non_interactive)
    )


def _print_context(context: CalendarContext, tz) -> None:
    print("Window        : {0}  ->  {1}".format(
        _fmt(context.range_start, tz), _fmt(context.range_end, tz)))
    print("Timezone      : {0}".format(getattr(tz, "key", tz)))
    print("Busy blocks   : {0}".format(len(context.busy_blocks)))
    print("Known events  : {0}".format(len(context.existing_events)))

    if context.busy_blocks:
        print("\nBusy:")
        for block in context.busy_blocks:
            print("  {0}".format(_fmt_slot(block, tz)))

    if context.existing_events:
        print("\nEvents:")
        for event in context.existing_events:
            marker = "[all-day] " if event.get("all_day") else ""
            print("  {0:<26}  {1}{2}".format(
                _fmt(event.get("start"), tz),
                marker,
                _truncate(event.get("summary", ""), 48),
            ))


def _print_slots(slots: List[CalendarSlot], tz, duration: int) -> None:
    if not slots:
        print("\nNo {0}-minute slot fits in this window within working hours.".format(duration))
        print("(Caller decides the fallback wording — the contract returns [].)")
        return
    print("\nSuggested slots ({0} min):".format(duration))
    for index, slot in enumerate(slots, start=1):
        print("  {0}. {1}".format(index, _fmt_slot(slot, tz)))


# --- commands --------------------------------------------------------------

def cmd_auth(args: argparse.Namespace) -> int:
    """Run or refresh OAuth and confirm which calendar was authorized."""
    service = get_calendar_service()
    from .calendar_auth import get_calendar_metadata

    meta = get_calendar_metadata(service)
    print("Authorized as : {0}".format(meta.get("id", "?")))
    print("Calendar      : {0}".format(meta.get("summary", "?")))
    print("Timezone      : {0}".format(meta.get("timeZone", "?")))
    print("Token stored  : {0}".format(config.TOKEN_FILE))
    print("Scopes        : {0}".format("\n                ".join(config.SCOPES)))
    print("\nNote: this token also grants the write scope (calendar.events).")
    print("`create`/`update`/`cancel` will write to this calendar for real.")
    return 0


def cmd_intent(args: argparse.Namespace) -> int:
    """Run the scheduling gate over the sample emails. No network at all."""
    emails = samples.ALL_SAMPLES
    if args.id:
        email = samples.SAMPLES_BY_ID.get(args.id)
        if email is None:
            print("Unknown sample id {0!r}. Known: {1}".format(
                args.id, ", ".join(samples.SAMPLES_BY_ID)), file=sys.stderr)
            return 1
        emails = [email]

    print("{0:<9}  {1:<8}  {2:<5}  {3:<44}  {4}".format(
        "ID", "VERDICT", "SCORE", "SUBJECT", "WHY"))
    print("{0}  {1}  {2}  {3}  {4}".format("-" * 9, "-" * 8, "-" * 5, "-" * 44, "-" * 30))
    for email in emails:
        signals = scheduling_signals(email)
        print("{0:<9}  {1:<8}  {2:<5}  {3:<44}  {4}".format(
            email.email_id,
            "SCHED" if signals.is_scheduling_related else "-",
            signals.score,
            _truncate(email.subject, 44),
            _truncate("; ".join(signals.reasons) or "no signals", 60),
        ))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """Print free/busy blocks and existing events for the window."""
    context = _load_context(args)
    _print_context(context, get_timezone(_resolve_timezone(args)))
    return 0


def cmd_slots(args: argparse.Namespace) -> int:
    """Print proposed open slots for the window."""
    context = _load_context(args)
    tz_name = _resolve_timezone(args)
    slots = suggest_available_slots(
        args.duration,
        working_hours=args.hours,
        context=context,
        max_slots=args.max_slots,
        include_weekends=args.weekends,
        timezone_name=tz_name,
    )
    _print_slots(slots, get_timezone(tz_name), args.duration)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """The Phase 1B acceptance check: a fake scheduling email in, calendar
    context + suggested slots out."""
    email = samples.SAMPLES_BY_ID.get(args.id) if args.id else samples.DIRECT_ASK
    if email is None:
        print("Unknown sample id {0!r}. Known: {1}".format(
            args.id, ", ".join(samples.SAMPLES_BY_ID)), file=sys.stderr)
        return 1

    print("=" * 72)
    print("SAMPLE EMAIL")
    print("=" * 72)
    print("id      : {0}".format(email.email_id))
    print("from    : {0}".format(email.sender))
    print("subject : {0}".format(email.subject))
    for name, value in email.headers.items():
        if name.lower() in {"content-type", "list-unsubscribe", "precedence", "auto-submitted"}:
            print("{0:<8}: {1}".format(name.lower()[:8], _truncate(value, 60)))
    print("\n{0}".format(textwrap.indent(_truncate(email.body, 400), "  ")))

    signals = scheduling_signals(email)
    print("\n" + "=" * 72)
    print("STEP 1 — scheduling intent gate (no API call)")
    print("=" * 72)
    print("is_scheduling_related : {0}".format(signals.is_scheduling_related))
    print("score                 : {0} (threshold {1})".format(signals.score, 2))
    print("matched               : {0}".format("; ".join(signals.reasons) or "nothing"))

    if not signals.is_scheduling_related:
        print("\nGate returned False — no Calendar API call is made for this email.")
        print("That is the point of the gate: most mail never touches the calendar.")
        return 0

    context = _load_context(args)
    tz_name = _resolve_timezone(args)
    tz = get_timezone(tz_name)

    print("\n" + "=" * 72)
    print("STEP 2 — calendar context")
    print("=" * 72)
    _print_context(context, tz)

    print("\n" + "=" * 72)
    print("STEP 3 — suggested slots (folded into Track C's reply outline)")
    print("=" * 72)
    slots = suggest_available_slots(
        args.duration,
        working_hours=args.hours,
        context=context,
        max_slots=args.max_slots,
        include_weekends=args.weekends,
        timezone_name=tz_name,
    )
    _print_slots(slots, tz, args.duration)

    if slots:
        print("\nAs an outline bullet Track C could use:")
        options = " or ".join(_fmt_slot(s, tz) for s in slots)
        print('  • Offer availability: {0}'.format(options))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """Create a real event — no --offline for writes; this proves the
    plumbing against your actual calendar."""
    missing = [name for name in ("summary", "start", "end") if getattr(args, name) is None]
    if missing:
        print("create requires --{0}".format(", --".join(missing)), file=sys.stderr)
        return 2
    service = get_calendar_service(allow_interactive=not args.non_interactive)
    proposed = ProposedEvent(
        title=args.summary,
        start=args.start,
        end=args.end,
        attendees=args.attendee or [],
        location=args.location,
        description=args.description,
    )
    event = create_event(proposed, calendar_id=args.calendar_id, service=service)
    tz = get_timezone(get_calendar_timezone(service, args.calendar_id))
    _print_created_event(event, tz)
    return 0 if event.error is None else 1


def cmd_update(args: argparse.Namespace) -> int:
    """Rename/reschedule/edit an existing event. Only the flags you pass change."""
    service = get_calendar_service(allow_interactive=not args.non_interactive)
    event = update_event(
        args.event_id,
        summary=args.summary,
        start=args.start,
        end=args.end,
        description=args.description,
        location=args.location,
        attendees=args.attendee,
        calendar_id=args.calendar_id,
        service=service,
    )
    _print_raw_event(event)
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel (delete) an event. Idempotent — canceling twice is not an error."""
    service = get_calendar_service(allow_interactive=not args.non_interactive)
    delete_event(args.event_id, calendar_id=args.calendar_id, service=service)
    print("Cancelled {0} on calendar {1}.".format(args.event_id, args.calendar_id))
    return 0


# --- entry point -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m calendaring.cli",
        description="Google Calendar context, slot suggestion, and event writes "
                    "(Track A, Phase 1B + calendar writes). Read commands never "
                    "touch your calendar; create/update/cancel do.",
    )
    # Shared flags live on a parent parser so they can be typed *after* the
    # subcommand (`... slots --offline`), which is what people actually expect.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        help="show retry/backoff and degradation logging")
    common.add_argument("--offline", action="store_true",
                        help="use canned free/busy data instead of the API")
    common.add_argument("--non-interactive", action="store_true",
                        help="fail instead of opening a browser if no valid token exists")

    window = argparse.ArgumentParser(add_help=False)
    window.add_argument("-d", "--days", type=int, default=config.DEFAULT_WINDOW_DAYS,
                        help="window length in days (default: {0})".format(config.DEFAULT_WINDOW_DAYS))

    slotting = argparse.ArgumentParser(add_help=False)
    slotting.add_argument("--duration", type=int, default=config.DEFAULT_DURATION_MINUTES,
                          help="meeting length in minutes (default: {0})".format(config.DEFAULT_DURATION_MINUTES))
    slotting.add_argument("--hours", type=_hours, default=config.WORKING_HOURS,
                          metavar="START-END",
                          help="working hours in the calendar's timezone (default: {0}-{1})".format(*config.WORKING_HOURS))
    slotting.add_argument("--max-slots", type=int, default=config.DEFAULT_MAX_SLOTS,
                          help="how many slots to propose (default: {0})".format(config.DEFAULT_MAX_SLOTS))
    slotting.add_argument("--weekends", action="store_true", default=config.INCLUDE_WEEKENDS,
                          help="allow weekend slots")

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", parents=[common], help="run or refresh Calendar OAuth consent")
    p_auth.set_defaults(func=cmd_auth)

    p_intent = sub.add_parser("intent", parents=[common],
                              help="run the scheduling gate over sample emails (no network)")
    p_intent.add_argument("--id", help="only this sample id")
    p_intent.set_defaults(func=cmd_intent)

    p_context = sub.add_parser("context", parents=[common, window],
                               help="print free/busy blocks and events")
    p_context.set_defaults(func=cmd_context)

    p_slots = sub.add_parser("slots", parents=[common, window, slotting],
                             help="print proposed open slots")
    p_slots.set_defaults(func=cmd_slots)

    p_demo = sub.add_parser("demo", parents=[common, window, slotting],
                            help="fake scheduling email -> context + suggested slots")
    p_demo.add_argument("--id", help="sample id (default: sched-1)")
    p_demo.set_defaults(func=cmd_demo)

    event_common = argparse.ArgumentParser(add_help=False)
    event_common.add_argument("--summary", help="event title")
    event_common.add_argument("--start", type=_parse_dt, help="ISO datetime, e.g. 2026-08-25T14:00:00")
    event_common.add_argument("--end", type=_parse_dt, help="ISO datetime, e.g. 2026-08-25T14:30:00")
    event_common.add_argument("--description", help="event description")
    event_common.add_argument("--location", help="event location")
    event_common.add_argument("--attendee", action="append", metavar="EMAIL",
                              help="attendee email; repeat for more than one")
    event_common.add_argument("--calendar-id", default="primary",
                              help="calendar to write to (default: primary)")

    p_create = sub.add_parser("create", parents=[common, event_common],
                              help="create a real calendar event")
    p_create.set_defaults(func=cmd_create)

    p_update = sub.add_parser("update", parents=[common, event_common],
                              help="rename/reschedule/edit an existing event")
    p_update.add_argument("event_id")
    p_update.set_defaults(func=cmd_update)

    p_cancel = sub.add_parser("cancel", parents=[common],
                              help="cancel (delete) an event")
    p_cancel.add_argument("event_id")
    p_cancel.add_argument("--calendar-id", default="primary",
                          help="calendar the event is on (default: primary)")
    p_cancel.set_defaults(func=cmd_cancel)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except MissingCredentialsError as exc:
        print("\n{0}\n".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Normal when piping into `head`/`less` — the reader closed early.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
