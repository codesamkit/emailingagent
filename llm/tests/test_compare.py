"""The comparison harness, including the clustering diagnostic."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llm.compare import Comparison, StageResult, compare, run_stage, score_spread
from models.schema import RawEmail, ReadStatus

NOW = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)


def email(email_id="e1") -> RawEmail:
    return RawEmail(
        email_id=email_id, thread_id="t1", sender="a@b.example",
        recipients=["me@example.com"], subject="Hi", body="Body",
        received_at=NOW, read_status=ReadStatus.READ,
    )


def scored(*values) -> list:
    return [
        StageResult("ollama", "gemma2:2b", "e{0}".format(i),
                    value={"score": v, "level": "medium", "justification": "x"})
        for i, v in enumerate(values)
    ]


class TestScoreSpread:
    def test_reports_range_and_distinct_values(self):
        spread = score_spread(scored(10.0, 40.0, 90.0))
        assert spread["min"] == 10.0
        assert spread["max"] == 90.0
        assert spread["distinct"] == 3
        assert spread["range"] == 80.0

    def test_clustered_scores_show_near_zero_stdev(self):
        """The small-model failure mode: plausible values, no ranking signal."""
        spread = score_spread(scored(70.0, 70.0, 70.0, 68.0, 70.0))
        assert spread["distinct"] == 2
        assert spread["stdev"] < 1.0

    def test_well_separated_scores_show_real_stdev(self):
        assert score_spread(scored(5.0, 30.0, 60.0, 95.0))["stdev"] > 20

    def test_empty_results(self):
        assert score_spread([])["count"] == 0

    def test_failed_rows_are_excluded(self):
        rows = scored(50.0) + [StageResult("ollama", "m", "e9", error="boom")]
        assert score_spread(rows)["count"] == 1


class TestRunStage:
    def test_client_init_failure_yields_error_rows_not_an_exception(self, monkeypatch):
        """A down backend must still produce a comparable row."""
        import llm.compare as mod

        monkeypatch.setattr(mod, "get_client", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no credentials")))
        rows = run_stage("summarize", [email("a"), email("b")], "anthropic")
        assert len(rows) == 2
        assert all(not r.ok for r in rows)
        assert "no credentials" in rows[0].error

    def test_per_email_failure_does_not_abort_the_rest(self, monkeypatch):
        import llm.compare as mod

        monkeypatch.setattr(mod, "get_client", lambda *a, **k: object())

        def flaky(e, client):
            if e.email_id == "b":
                raise RuntimeError("boom")
            return "summary"

        monkeypatch.setitem(mod.STAGE_RUNNERS, "summarize", flaky)
        rows = run_stage("summarize", [email("a"), email("b"), email("c")], "ollama")
        assert [r.ok for r in rows] == [True, False, True]

    def test_timing_is_recorded(self, monkeypatch):
        import llm.compare as mod

        monkeypatch.setattr(mod, "get_client", lambda *a, **k: object())
        monkeypatch.setitem(mod.STAGE_RUNNERS, "summarize", lambda e, c: "s")
        assert run_stage("summarize", [email()], "ollama")[0].seconds >= 0

    def test_progress_callback(self, monkeypatch):
        import llm.compare as mod

        monkeypatch.setattr(mod, "get_client", lambda *a, **k: object())
        monkeypatch.setitem(mod.STAGE_RUNNERS, "summarize", lambda e, c: "s")
        seen = []
        run_stage("summarize", [email("a"), email("b")], "ollama",
                  on_progress=lambda d, t: seen.append((d, t)))
        assert seen == [(1, 2), (2, 2)]


class TestCompare:
    def test_runs_every_provider_over_the_same_emails(self, monkeypatch):
        import llm.compare as mod

        monkeypatch.setattr(mod, "get_client", lambda *a, **k: object())
        monkeypatch.setitem(mod.STAGE_RUNNERS, "summarize",
                            lambda e, c: "summary of " + e.email_id)
        result = compare("summarize", [email("a"), email("b")],
                         providers=("ollama", "anthropic"))
        assert set(result.providers()) == {"ollama", "anthropic"}
        assert len(result.results["ollama"]) == 2
        assert result.failures("ollama") == 0

    def test_unknown_stage_is_rejected(self):
        with pytest.raises(ValueError):
            compare("nonsense", [email()])
