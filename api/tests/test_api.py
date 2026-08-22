"""API behavior against a temporary database — no LLM, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import main
from models.schema import (
    CalendarContext,
    CalendarSlot,
    ImportanceLevel,
    ProcessedEmail,
    ReadStatus,
    ReplyOutlineStatus,
)
from pipeline import persist

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def email(email_id, **overrides) -> ProcessedEmail:
    defaults = dict(
        email_id=email_id, thread_id="t-" + email_id, sender="Dana <dana@example.com>",
        subject="Subject " + email_id, received_at=NOW, read_status=ReadStatus.READ,
        is_no_reply=False, importance_score=50.0, importance_level=ImportanceLevel.MEDIUM,
        summary="A summary.", is_scheduling_related=False, processed_at=NOW,
    )
    defaults.update(overrides)
    return ProcessedEmail(**defaults)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(main, "DB_PATH", db_path)
    persist.upsert(
        [
            email("read-eligible", reply_outline=["Existing bullet"],
                  reply_outline_status=ReplyOutlineStatus.SUGGESTED, importance_score=90.0,
                  importance_level=ImportanceLevel.URGENT),
            email("unread", read_status=ReadStatus.UNREAD, importance_score=70.0,
                  importance_level=ImportanceLevel.HIGH),
            email("no-reply", is_no_reply=True, no_reply_reason="no-reply@ pattern",
                  reply_outline_status=ReplyOutlineStatus.NOT_APPLICABLE,
                  importance_score=10.0, importance_level=ImportanceLevel.LOW),
            email("scheduling", is_scheduling_related=True, importance_score=60.0,
                  sender="Sam <sam@other.example>", subject="Coffee next week?",
                  calendar_context=CalendarContext(
                      range_start=NOW, range_end=NOW + timedelta(days=7),
                      suggested_slots=[CalendarSlot(NOW, NOW + timedelta(minutes=30))],
                      existing_events=[{"summary": "Standup", "start": NOW, "all_day": False}],
                  )),
            email("unscored", importance_score=None, importance_level=None, processed_at=None),
        ],
        db_path,
    )
    return TestClient(main.app)


class TestHealthAndStats:
    def test_health_reports_row_count(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["processedEmails"] == 5

    def test_stats_counts(self, client):
        body = client.get("/api/stats").json()
        assert body["total"] == 5
        assert body["unread"] == 1
        assert body["noReply"] == 1
        assert body["scheduling"] == 1
        assert body["withOutline"] == 1
        assert body["unprocessed"] == 1
        assert body["byLevel"]["urgent"] == 1


class TestListEmails:
    def test_returns_all_by_default(self, client):
        body = client.get("/api/emails").json()
        assert body["total"] == 5
        assert len(body["emails"]) == 5

    def test_sorted_by_importance_descending(self, client):
        ids = [e["emailId"] for e in client.get("/api/emails").json()["emails"]]
        assert ids[0] == "read-eligible"   # 90
        assert ids[-1] == "unscored"       # None sorts last

    def test_unscored_sorts_last_even_ascending(self, client):
        """Unscored means 'not known', not 'least important'."""
        ids = [e["emailId"] for e in
               client.get("/api/emails?descending=false").json()["emails"]]
        assert ids[-1] == "unscored"

    def test_sort_by_received(self, client):
        assert client.get("/api/emails?sortBy=received").status_code == 200

    def test_invalid_sort_is_rejected(self, client):
        assert client.get("/api/emails?sortBy=nonsense").status_code == 422

    def test_filter_unread(self, client):
        body = client.get("/api/emails?readStatus=unread").json()
        assert [e["emailId"] for e in body["emails"]] == ["unread"]

    def test_filter_no_reply_true(self, client):
        body = client.get("/api/emails?noReply=true").json()
        assert [e["emailId"] for e in body["emails"]] == ["no-reply"]

    def test_filter_no_reply_false_excludes_unclassified(self, client):
        """noReply=false means 'known not to be', not 'not known to be'."""
        ids = [e["emailId"] for e in client.get("/api/emails?noReply=false").json()["emails"]]
        assert "no-reply" not in ids
        assert "read-eligible" in ids

    def test_filter_scheduling(self, client):
        body = client.get("/api/emails?scheduling=true").json()
        assert [e["emailId"] for e in body["emails"]] == ["scheduling"]

    def test_filter_has_outline(self, client):
        body = client.get("/api/emails?hasOutline=true").json()
        assert [e["emailId"] for e in body["emails"]] == ["read-eligible"]

    def test_filter_by_importance_level(self, client):
        body = client.get("/api/emails?importance=urgent").json()
        assert [e["emailId"] for e in body["emails"]] == ["read-eligible"]

    def test_search_matches_subject_and_sender(self, client):
        assert client.get("/api/emails?search=coffee").json()["total"] == 1
        assert client.get("/api/emails?search=sam@other").json()["total"] == 1

    def test_filters_combine(self, client):
        assert client.get("/api/emails?readStatus=read&noReply=true").json()["total"] == 1

    def test_total_reflects_the_filtered_set_not_the_page(self, client):
        body = client.get("/api/emails?limit=1").json()
        assert body["total"] == 5
        assert len(body["emails"]) == 1

    def test_pagination_offset(self, client):
        first = client.get("/api/emails?limit=1&offset=0").json()["emails"][0]
        second = client.get("/api/emails?limit=1&offset=1").json()["emails"][0]
        assert first["emailId"] != second["emailId"]

    def test_limit_bounds_are_enforced(self, client):
        assert client.get("/api/emails?limit=0").status_code == 422
        assert client.get("/api/emails?limit=99999").status_code == 422


class TestEligibilityIsServerComputed:
    def test_eligible_email_is_marked_eligible(self, client):
        body = client.get("/api/emails/read-eligible").json()
        assert body["outlineEligible"] is True

    def test_unread_is_not_eligible(self, client):
        assert client.get("/api/emails/unread").json()["outlineEligible"] is False

    def test_no_reply_is_not_eligible(self, client):
        body = client.get("/api/emails/no-reply").json()
        assert body["outlineEligible"] is False
        assert body["replyOutlineStatus"] == "not_applicable"


class TestGetEmail:
    def test_includes_calendar_context(self, client):
        body = client.get("/api/emails/scheduling").json()
        assert body["calendarContext"] is not None
        assert len(body["calendarContext"]["suggestedSlots"]) == 1
        assert body["calendarContext"]["events"][0]["summary"] == "Standup"

    def test_list_view_omits_calendar_payload(self, client):
        """The list must not ship every email's full calendar blob."""
        body = client.get("/api/emails?scheduling=true").json()
        assert body["emails"][0]["calendarContext"] is None
        assert body["emails"][0]["hasCalendarContext"] is True

    def test_unknown_id_is_404(self, client):
        assert client.get("/api/emails/nope").status_code == 404

    def test_detail_includes_original_body_from_raw_email(self, client):
        from ingestion.models import RawEmail
        from ingestion import store

        store.upsert_emails(
            [RawEmail(
                email_id="read-eligible", thread_id="t-read-eligible",
                sender="Dana <dana@example.com>", recipients=["me@example.com"],
                subject="Subject read-eligible", body="Hi,\n\nOriginal message text.",
                received_at=NOW, read_status=ReadStatus.READ,
            )],
            main.DB_PATH,
        )
        body = client.get("/api/emails/read-eligible").json()
        assert body["body"] == "Hi,\n\nOriginal message text."

    def test_detail_body_is_null_when_raw_email_is_gone(self, client):
        body = client.get("/api/emails/unread").json()
        assert body["body"] is None


class TestEditOutline:
    def test_saves_and_marks_edited(self, client):
        response = client.patch("/api/emails/read-eligible/outline",
                                json={"outline": ["Edited bullet", "Second"]})
        assert response.status_code == 200
        body = response.json()
        assert body["replyOutline"] == ["Edited bullet", "Second"]
        assert body["replyOutlineStatus"] == "edited"

    def test_blank_bullets_are_stripped(self, client):
        body = client.patch("/api/emails/read-eligible/outline",
                            json={"outline": ["Real", "   ", ""]}).json()
        assert body["replyOutline"] == ["Real"]

    def test_rejects_edit_on_unread_email(self, client):
        """The gate is a correctness rule, not a UI affordance — a drifted
        client or a direct API call must not get past it."""
        response = client.patch("/api/emails/unread/outline", json={"outline": ["x"]})
        assert response.status_code == 409
        assert "not eligible" in response.json()["detail"]

    def test_rejects_edit_on_no_reply_email(self, client):
        response = client.patch("/api/emails/no-reply/outline", json={"outline": ["x"]})
        assert response.status_code == 409

    def test_unknown_id_is_404(self, client):
        assert client.patch("/api/emails/nope/outline", json={"outline": ["x"]}).status_code == 404


class TestIndexPage:
    def test_root_serves_the_review_ui(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Structural markers, not the product name — the page's branding is
        # free to change without breaking the API contract.
        assert 'id="queue"' in response.text
        assert 'id="detail"' in response.text


class TestExpandDraft:
    def test_expands_outline_into_a_draft(self, client, monkeypatch):
        """Returns prose for the user to review — nothing is sent."""
        import json as _json

        from drafting import expand as expand_module

        class _Block:
            type = "text"
            text = _json.dumps({"draft": "Hi,\n\nConfirmed.\n\nBest,"})

        class _Fake:
            class messages:
                @staticmethod
                def create(**kwargs):
                    return type("R", (), {"content": [_Block()]})()

        monkeypatch.setattr(expand_module, "_get_default_client", lambda: _Fake())

        response = client.post("/api/emails/read-eligible/expand")
        assert response.status_code == 200
        assert response.json()["draft"].startswith("Hi,")

    def test_email_without_outline_is_409(self, client):
        assert client.post("/api/emails/unread/expand").status_code == 409

    def test_unknown_id_is_404(self, client):
        assert client.post("/api/emails/nope/expand").status_code == 404
