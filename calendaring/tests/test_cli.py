"""CLI smoke tests — every command runs offline, with no consent and no network."""

from __future__ import annotations

import pytest

from calendaring import cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


class TestIntentCommand:
    def test_lists_every_sample_with_a_verdict(self, capsys):
        code, out = run(capsys, "intent")
        assert code == 0
        assert "sched-1" in out and "plain-2" in out
        assert "SCHED" in out

    def test_single_sample(self, capsys):
        code, out = run(capsys, "intent", "--id", "sched-2")
        assert code == 0
        assert "sched-2" in out and "sched-1" not in out

    def test_unknown_id_is_an_error(self, capsys):
        assert cli.main(["intent", "--id", "nope"]) == 1


class TestContextCommand:
    def test_offline_prints_busy_and_events(self, capsys):
        code, out = run(capsys, "context", "--offline")
        assert code == 0
        assert "Busy blocks" in out and "Known events" in out
        assert "Team offsite" in out

    def test_window_length_is_configurable(self, capsys):
        code, out = run(capsys, "context", "--offline", "--days", "14")
        assert code == 0
        assert "Window" in out


class TestSlotsCommand:
    def test_offline_prints_suggestions(self, capsys):
        code, out = run(capsys, "slots", "--offline")
        assert code == 0
        assert "Suggested slots" in out

    def test_duration_is_reflected(self, capsys):
        code, out = run(capsys, "slots", "--offline", "--duration", "45")
        assert code == 0
        assert "45 min" in out

    def test_impossible_working_hours_rejected(self):
        with pytest.raises(SystemExit):
            cli.main(["slots", "--offline", "--hours", "17-9"])

    def test_malformed_working_hours_rejected(self):
        with pytest.raises(SystemExit):
            cli.main(["slots", "--offline", "--hours", "nine-five"])


class TestDemoCommand:
    def test_full_walkthrough_for_a_scheduling_email(self, capsys):
        """The Phase 1B acceptance check, end to end."""
        code, out = run(capsys, "demo", "--offline")
        assert code == 0
        assert "SAMPLE EMAIL" in out
        assert "is_scheduling_related : True" in out
        assert "STEP 2 — calendar context" in out
        assert "Suggested slots" in out
        assert "Offer availability" in out

    def test_non_scheduling_email_stops_before_any_api_call(self, capsys):
        code, out = run(capsys, "demo", "--offline", "--id", "plain-3")
        assert code == 0
        assert "is_scheduling_related : False" in out
        assert "no Calendar API call is made" in out
        assert "STEP 2" not in out

    def test_calendar_invite_sample(self, capsys):
        code, out = run(capsys, "demo", "--offline", "--id", "sched-3")
        assert code == 0
        assert "calendar invite" in out

    def test_unknown_id_is_an_error(self):
        assert cli.main(["demo", "--offline", "--id", "nope"]) == 1


class TestParser:
    def test_subcommand_is_required(self):
        with pytest.raises(SystemExit):
            cli.main([])

    def test_shared_flags_accepted_after_the_subcommand(self, capsys):
        code, _ = run(capsys, "slots", "--offline", "--max-slots", "1")
        assert code == 0
