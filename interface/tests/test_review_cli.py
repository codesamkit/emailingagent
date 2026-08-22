from __future__ import annotations

import pytest

from interface.fixtures import demo_processed_emails
from interface.review_cli import (
    NO_REPLY_BADGE,
    edit_outline,
    expand_to_full_draft,
    find_email,
    format_outline_section,
)
from models.schema import ReplyOutlineStatus


def test_format_outline_section_shows_bullets_when_present():
    email = find_email(demo_processed_emails(), "demo-eligible")
    text = format_outline_section(email)
    assert "Acknowledge the request" in text


def test_format_outline_section_shows_placeholder_for_unread():
    email = find_email(demo_processed_emails(), "demo-unread")
    assert format_outline_section(email) == "  (Unread — no outline yet)"


def test_format_outline_section_shows_placeholder_for_no_reply():
    email = find_email(demo_processed_emails(), "demo-no-reply")
    assert format_outline_section(email) == "  (No-Reply — no response needed)"


def test_no_reply_badge_text_matches_spec():
    assert NO_REPLY_BADGE == "No-Reply — informational only"


def test_find_email_raises_for_unknown_id():
    with pytest.raises(KeyError):
        find_email(demo_processed_emails(), "does-not-exist")


def test_edit_outline_replaces_bullets_and_marks_edited():
    email = find_email(demo_processed_emails(), "demo-eligible")
    edit_outline(email, ["Custom bullet one", "Custom bullet two"])
    assert email.reply_outline == ["Custom bullet one", "Custom bullet two"]
    assert email.reply_outline_status == ReplyOutlineStatus.EDITED


def test_edit_outline_refuses_when_no_outline_exists():
    email = find_email(demo_processed_emails(), "demo-unread")
    with pytest.raises(ValueError):
        edit_outline(email, ["Should not be allowed"])


def test_expand_to_full_draft_reports_stub_as_not_yet_available():
    result = expand_to_full_draft("demo-eligible")
    assert "isn't implemented" in result.lower()
