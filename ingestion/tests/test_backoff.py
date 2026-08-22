"""Retry policy tests — no real sleeping, no real network."""

from __future__ import annotations

import json

import pytest
from googleapiclient.errors import HttpError

from ingestion import backoff


class FakeResponse(dict):
    """Stands in for an httplib2 Response: a dict of headers, plus attributes.

    `HttpError.__init__` reads `.reason`, so the double must provide it.
    """

    def __init__(self, status, **headers):
        super().__init__(**headers)
        self.status = status
        self.reason = "Test Error"


def http_error(status, reason=None, **headers):
    body = {"error": {"errors": [{"reason": reason}] if reason else []}}
    return HttpError(
        FakeResponse(status, **headers), json.dumps(body).encode("utf-8")
    )


class Recorder:
    """Collects the delays `with_retry` would have slept for."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


class TestIsRetryable:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_transient_statuses_retry(self, status):
        assert backoff.is_retryable(http_error(status)) is True

    def test_rate_limited_403_retries(self):
        assert backoff.is_retryable(http_error(403, "rateLimitExceeded")) is True
        assert backoff.is_retryable(http_error(403, "userRateLimitExceeded")) is True

    def test_permission_403_does_not_retry(self):
        # Retrying this would hide a bad scope behind 30 seconds of silence.
        assert backoff.is_retryable(http_error(403, "insufficientPermissions")) is False

    @pytest.mark.parametrize("status", [400, 401, 404])
    def test_client_errors_do_not_retry(self, status):
        assert backoff.is_retryable(http_error(status)) is False

    def test_connection_errors_retry(self):
        assert backoff.is_retryable(ConnectionError("reset")) is True

    def test_unrelated_exceptions_do_not_retry(self):
        assert backoff.is_retryable(ValueError("nope")) is False


class TestWithRetry:
    def test_returns_immediately_on_success(self):
        sleeper = Recorder()
        assert backoff.with_retry(lambda: "ok", sleep=sleeper) == "ok"
        assert sleeper.delays == []

    def test_retries_then_succeeds(self):
        calls = {"n": 0}
        sleeper = Recorder()

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise http_error(429)
            return "recovered"

        assert backoff.with_retry(flaky, sleep=sleeper) == "recovered"
        assert calls["n"] == 3
        assert len(sleeper.delays) == 2

    def test_gives_up_after_max_retries(self):
        calls = {"n": 0}

        def always_429():
            calls["n"] += 1
            raise http_error(429)

        with pytest.raises(HttpError):
            backoff.with_retry(always_429, max_retries=2, sleep=Recorder())
        assert calls["n"] == 3  # initial attempt + 2 retries

    def test_non_retryable_error_raises_without_sleeping(self):
        sleeper = Recorder()

        def forbidden():
            raise http_error(403, "insufficientPermissions")

        with pytest.raises(HttpError):
            backoff.with_retry(forbidden, sleep=sleeper)
        assert sleeper.delays == []

    def test_honors_retry_after_header(self):
        sleeper = Recorder()
        calls = {"n": 0}

        def limited():
            calls["n"] += 1
            if calls["n"] == 1:
                raise http_error(429, **{"retry-after": "7"})
            return "ok"

        assert backoff.with_retry(limited, sleep=sleeper) == "ok"
        assert sleeper.delays == [7.0]

    def test_backoff_window_grows_and_is_capped(self):
        windows = [backoff._delay_for(attempt) for attempt in range(10)]
        assert all(0.0 <= w <= 32.0 for w in windows)
        assert max(windows[6:]) <= 32.0
