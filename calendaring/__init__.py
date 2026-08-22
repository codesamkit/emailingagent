"""Track A — Google Calendar integration (Phase 1B).

Reads free/busy blocks and existing events for a date window, proposes open
meeting slots inside working hours, and offers a cheap keyword/MIME intent
check so callers can decide whether an email is worth a Calendar API call at
all.

Read-only in behavior: the write scope is requested at consent time (so the
user is not asked to re-consent when event creation lands in a later phase),
but no code path in this package creates, updates, or deletes anything.

NOTE ON THE PACKAGE NAME: `FILE-TREE.md` originally proposed `calendar/`.
A top-level package by that name shadows the standard library's `calendar`
module, which `http.cookiejar` imports (`from calendar import timegm`) —
that breaks `requests`, which breaks `google-auth`'s transport, surfacing as
a misleading "The requests library is not installed". Hence `calendaring`.
"""
