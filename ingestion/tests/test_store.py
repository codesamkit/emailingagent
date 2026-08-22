"""Persistence tests: schema, idempotent upsert, read-back."""

from __future__ import annotations

import json
import sqlite3

import pytest

from datetime import datetime

from models.schema import ReadStatus

from ingestion import store
from ingestion.models import RawEmail


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    store.init_db(path)
    return path


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def make_email(email_id="m1", read_status="unread", subject="Hello", **overrides):
    defaults = dict(
        email_id=email_id,
        thread_id="t1",
        sender="Dana Reed <dana@example.com>",
        recipients=["me@example.com"],
        subject=subject,
        body="Body text.",
        snippet="Body",
        received_at=at("2025-08-22T12:00:00+00:00"),
        read_status=ReadStatus(read_status),
        label_ids=["INBOX", "UNREAD"] if read_status == "unread" else ["INBOX"],
        headers={"From": "Dana Reed <dana@example.com>", "Precedence": "bulk"},
        has_attachments=False,
    )
    defaults.update(overrides)
    return RawEmail(**defaults)


class TestSchema:
    def test_init_is_idempotent(self, db):
        store.init_db(db)
        store.init_db(db)
        assert store.count(db) == 0

    def test_read_status_is_constrained(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            with store.connect(db) as conn:
                conn.execute(
                    "INSERT INTO raw_email (email_id, thread_id, sender, received_at,"
                    " read_status, label_ids, headers, fetched_at)"
                    " VALUES ('x','t','s','2025-01-01','maybe','[]','{}','2025-01-01')"
                )
                conn.commit()


class TestUpsert:
    def test_stores_and_reads_back(self, db):
        store.upsert_emails([make_email()], db)
        assert store.count(db) == 1
        got = store.get("m1", db)
        assert got.sender == "Dana Reed <dana@example.com>"
        assert got.headers["Precedence"] == "bulk"
        assert got.label_ids == ["INBOX", "UNREAD"]
        assert got.read_status == "unread"

    def test_reingesting_does_not_duplicate(self, db):
        store.upsert_emails([make_email()], db)
        store.upsert_emails([make_email()], db)
        assert store.count(db) == 1

    def test_reingesting_flips_read_status(self, db):
        # This is the behavior Track C's outline regeneration depends on.
        store.upsert_emails([make_email(read_status="unread")], db)
        store.upsert_emails([make_email(read_status="read")], db)
        assert store.count(db) == 1
        assert store.get("m1", db).read_status == "read"

    def test_empty_iterable_is_a_no_op(self, db):
        assert store.upsert_emails([], db) == 0
        assert store.count(db) == 0

    def test_round_trips_unicode_headers(self, db):
        email = make_email(headers={"From": "Jörg Müller <joerg@example.de>"})
        store.upsert_emails([email], db)
        assert store.get("m1", db).headers["From"] == "Jörg Müller <joerg@example.de>"

    def test_stamps_fetched_at(self, db):
        store.upsert_emails([make_email()], db)
        with store.connect(db) as conn:
            row = conn.execute("SELECT fetched_at FROM raw_email").fetchone()
        assert row["fetched_at"].endswith("+00:00")


class TestQueries:
    def test_recent_sorts_newest_first_and_limits(self, db):
        store.upsert_emails(
            [
                make_email("a", received_at=at("2025-08-01T00:00:00+00:00")),
                make_email("b", received_at=at("2025-08-03T00:00:00+00:00")),
                make_email("c", received_at=at("2025-08-02T00:00:00+00:00")),
            ],
            db,
        )
        assert [e.email_id for e in store.recent(2, db)] == ["b", "c"]

    def test_get_returns_none_when_absent(self, db):
        assert store.get("nope", db) is None

    def test_queries_work_on_a_fresh_database(self, tmp_path):
        # count/recent must not blow up before init_db has ever been called.
        fresh = tmp_path / "never-initialized.db"
        assert store.count(fresh) == 0
        assert store.recent(5, fresh) == []
