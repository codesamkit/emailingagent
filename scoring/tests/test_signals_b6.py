"""PHASES-COMPLEX.md B6: real (opt-in) owner identity and VIP detection.
No network, no Gmail auth, no real DB — resolve_account_owner's Gmail call
is monkeypatched to fail (simulating a test/CI environment with no
credentials, exactly what makes the graceful-fallback path load-bearing),
and compute_vip_senders runs against a fresh temp DB via B1's fixture
builder, not the real on-disk emails.db."""

from __future__ import annotations

import unittest

from retrieval.tests.fixtures import build_fixture_db
from scoring import signals


class TestResolveAccountOwner(unittest.TestCase):
    def setUp(self):
        signals._account_owner_cache.clear()

    def test_returns_none_when_gmail_auth_is_unavailable(self):
        def _raise(*args, **kwargs):
            raise RuntimeError("no credentials in this environment")

        original = None
        import ingestion.gmail_auth as gmail_auth

        original = gmail_auth.get_gmail_service
        gmail_auth.get_gmail_service = _raise
        try:
            owner = signals.resolve_account_owner(use_cache=False)
        finally:
            gmail_auth.get_gmail_service = original
        self.assertIsNone(owner)

    def test_result_is_cached_across_calls(self):
        calls = []

        def _fake_service(*args, **kwargs):
            calls.append(1)
            return object()

        import ingestion.gmail_auth as gmail_auth

        original_service = gmail_auth.get_gmail_service
        original_profile = gmail_auth.get_profile
        gmail_auth.get_gmail_service = _fake_service
        gmail_auth.get_profile = lambda service: {"emailAddress": "nidhi@example.com"}
        try:
            first = signals.resolve_account_owner()
            second = signals.resolve_account_owner()
        finally:
            gmail_auth.get_gmail_service = original_service
            gmail_auth.get_profile = original_profile

        self.assertEqual(first, "nidhi@example.com")
        self.assertEqual(second, "nidhi@example.com")
        self.assertEqual(len(calls), 1)  # second call served from cache


class TestComputeVipSenders(unittest.TestCase):
    def test_frequent_correspondents_are_flagged(self):
        path = build_fixture_db()
        vip = signals.compute_vip_senders(path, percentile=90.0)
        # alex@acme.example and jordan@acme.example are mentioned (as
        # header sender/recipient) across the most emails in the fixture.
        self.assertTrue(vip)
        self.assertTrue(vip & {"alex@acme.example", "jordan@acme.example"})

    def test_empty_db_returns_empty_set_not_an_error(self):
        import tempfile
        from pathlib import Path

        from models.db import init_db

        path = Path(tempfile.mkdtemp()) / "empty.db"
        init_db(path)
        self.assertEqual(signals.compute_vip_senders(path), frozenset())


if __name__ == "__main__":
    unittest.main()
