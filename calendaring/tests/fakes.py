"""Stand-ins for the Google Calendar API client — no network in any test.

Shaped like the real `googleapiclient` resource chain
(`service.freebusy().query(body=...).execute()`), so `context.py` is
exercised through exactly the call path it uses in production.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError


class FakeResponse(dict):
    """Stands in for an httplib2 Response: a dict of headers, plus attributes.

    `HttpError.__init__` reads `.reason`, so the double must provide it.
    """

    def __init__(self, status, **headers):
        super().__init__(**headers)
        self.status = status
        self.reason = "Test Error"


def http_error(status: int, reason: Optional[str] = None, **headers) -> HttpError:
    body = {"error": {"errors": [{"reason": reason}] if reason else []}}
    return HttpError(FakeResponse(status, **headers), json.dumps(body).encode("utf-8"))


class _Request:
    """A prepared API request whose `.execute()` returns or raises."""

    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, BaseException):
            raise self._result
        if callable(self._result):
            return self._result()
        return self._result


class FakeFreebusy:
    def __init__(self, owner: "FakeCalendarService"):
        self._owner = owner

    def query(self, body: Dict[str, Any]):
        self._owner.freebusy_calls.append(body)
        return _Request(self._owner._next(self._owner.freebusy_responses))


class FakeEvents:
    def __init__(self, owner: "FakeCalendarService"):
        self._owner = owner

    def list(self, **kwargs):
        self._owner.events_calls.append(kwargs)
        return _Request(self._owner._next(self._owner.events_responses))


class FakeCalendars:
    def __init__(self, owner: "FakeCalendarService"):
        self._owner = owner

    def get(self, calendarId: str):
        self._owner.calendars_calls.append(calendarId)
        return _Request(self._owner._next(self._owner.calendars_responses))


class FakeCalendarService:
    """A scripted Calendar service.

    Each `*_responses` list is consumed one entry per call; the last entry is
    reused once exhausted, so a single-element list means "always return this".
    An `Exception` entry is raised instead of returned, which is how retry and
    graceful-degradation paths are tested.
    """

    def __init__(
        self,
        freebusy_responses: Optional[List[Any]] = None,
        events_responses: Optional[List[Any]] = None,
        calendars_responses: Optional[List[Any]] = None,
    ):
        self.freebusy_responses = list(freebusy_responses or [{"calendars": {}}])
        self.events_responses = list(events_responses or [{"items": []}])
        self.calendars_responses = list(calendars_responses or [{"timeZone": "UTC"}])
        self.freebusy_calls: List[Dict[str, Any]] = []
        self.events_calls: List[Dict[str, Any]] = []
        self.calendars_calls: List[str] = []

    @staticmethod
    def _next(queue: List[Any]):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def freebusy(self):
        return FakeFreebusy(self)

    def events(self):
        return FakeEvents(self)

    def calendars(self):
        return FakeCalendars(self)


class ExplodingService:
    """Any use of this fails the test — proves no API call was made."""

    def __getattr__(self, name):
        raise AssertionError(
            "Calendar API was called (.{0}) when it should not have been".format(name)
        )


def busy(*pairs) -> Dict[str, Any]:
    """Build a freebusy response body from (start, end) RFC-3339 string pairs."""
    return {
        "calendars": {
            "primary": {"busy": [{"start": s, "end": e} for s, e in pairs]}
        }
    }
