"""Fetch-layer tests against a fake Gmail service — pagination, skips, storage.

Exercises the whole ingestion path (list -> get -> parse -> store) with no
network, so the pipeline is verifiable before real credentials exist.
"""

from __future__ import annotations

import json

import pytest
from googleapiclient.errors import HttpError

from ingestion import fetch, store
from ingestion.tests import fixtures as fx
from ingestion.tests.test_backoff import FakeResponse


class _Request:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Messages:
    def __init__(self, service):
        self._service = service

    def list(self, userId, maxResults, q=None, pageToken=None):
        self._service.list_calls.append(
            {"maxResults": maxResults, "q": q, "pageToken": pageToken}
        )
        page = self._service.pages.pop(0)
        return _Request(page)

    def get(self, userId, id, format):
        self._service.get_calls.append(id)
        result = self._service.messages.get(id)
        if result is None:
            result = HttpError(FakeResponse(404), b'{"error":{"errors":[]}}')
        return _Request(result)


class _Users:
    def __init__(self, service):
        self._service = service

    def messages(self):
        return _Messages(self._service)


class FakeGmail:
    """Minimal stand-in for the googleapiclient Gmail resource."""

    def __init__(self, pages, messages):
        self.pages = list(pages)
        self.messages = messages
        self.list_calls = []
        self.get_calls = []

    def users(self):
        return _Users(self)


def _page(ids, next_token=None):
    page = {"messages": [{"id": i} for i in ids]}
    if next_token:
        page["nextPageToken"] = next_token
    return page


class TestListMessageIds:
    def test_single_page(self):
        service = FakeGmail([_page(["a", "b", "c"])], {})
        assert fetch.list_message_ids(service, limit=10) == ["a", "b", "c"]

    def test_follows_pagination_until_limit(self):
        service = FakeGmail(
            [_page(["a", "b"], "tok1"), _page(["c", "d"], "tok2"), _page(["e"])], {}
        )
        assert fetch.list_message_ids(service, limit=5) == ["a", "b", "c", "d", "e"]
        assert [c["pageToken"] for c in service.list_calls] == [None, "tok1", "tok2"]

    def test_stops_at_limit_without_extra_pages(self):
        service = FakeGmail([_page(["a", "b", "c"], "tok1")], {})
        assert fetch.list_message_ids(service, limit=2) == ["a", "b"]
        assert len(service.list_calls) == 1

    def test_requests_no_more_than_the_remaining_count(self):
        service = FakeGmail([_page(["a"], "tok1"), _page(["b"])], {})
        fetch.list_message_ids(service, limit=2)
        assert [c["maxResults"] for c in service.list_calls] == [2, 1]

    def test_caps_page_size_at_the_gmail_maximum(self):
        service = FakeGmail([_page([])], {})
        fetch.list_message_ids(service, limit=5000)
        assert service.list_calls[0]["maxResults"] == 500

    def test_passes_the_query_through(self):
        service = FakeGmail([_page(["a"])], {})
        fetch.list_message_ids(service, limit=1, query="label:inbox")
        assert service.list_calls[0]["q"] == "label:inbox"

    def test_empty_inbox(self):
        service = FakeGmail([{}], {})
        assert fetch.list_message_ids(service, limit=10) == []


class TestFetchRecentEmails:
    def test_maps_messages_to_raw_emails(self):
        service = FakeGmail(
            [_page(["a", "b"])],
            {
                "a": fx.message("a", label_ids=["INBOX", "UNREAD"], payload=fx.PLAIN_ONLY),
                "b": fx.message("b", label_ids=["INBOX"], payload=fx.HTML_ONLY),
            },
        )
        emails = list(fetch.fetch_recent_emails(service, limit=2, query=""))
        assert [e.email_id for e in emails] == ["a", "b"]
        assert emails[0].read_status == "unread"
        assert emails[1].read_status == "read"
        assert emails[1].headers["List-Unsubscribe"].startswith("<https://")

    def test_skips_a_message_that_disappeared(self):
        # Deleted between list and get — log and continue, don't abort the run.
        service = FakeGmail(
            [_page(["a", "gone", "c"])],
            {
                "a": fx.message("a", payload=fx.PLAIN_ONLY),
                "c": fx.message("c", payload=fx.PLAIN_ONLY),
            },
        )
        emails = list(fetch.fetch_recent_emails(service, limit=3, query=""))
        assert [e.email_id for e in emails] == ["a", "c"]

    def test_reports_progress(self):
        service = FakeGmail(
            [_page(["a", "b"])],
            {"a": fx.message("a", payload=fx.PLAIN_ONLY),
             "b": fx.message("b", payload=fx.PLAIN_ONLY)},
        )
        seen = []
        list(fetch.fetch_recent_emails(
            service, limit=2, query="", on_progress=lambda d, t: seen.append((d, t))
        ))
        assert seen == [(1, 2), (2, 2)]


class TestEndToEnd:
    def test_fetch_then_store_then_read_back(self, tmp_path):
        db = tmp_path / "e2e.db"
        service = FakeGmail(
            [_page(["a"], "tok"), _page(["b"])],
            {
                "a": fx.message("a", label_ids=["INBOX", "UNREAD"], payload=fx.PLAIN_ONLY),
                "b": fx.message("b", label_ids=["INBOX"], payload=fx.NESTED_WITH_ATTACHMENT),
            },
        )
        emails = list(fetch.fetch_recent_emails(service, limit=2, query="label:inbox"))
        store.init_db(db)
        assert store.upsert_emails(emails, db) == 2
        assert store.count(db) == 2

        stored = store.get("b", db)
        assert stored.has_attachments is True
        assert stored.body_text == "See attached."
        assert stored.sender == "Ops <ops@example.com>"
