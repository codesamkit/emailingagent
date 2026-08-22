"""Parse-layer tests: MIME walking, HTML stripping, header capture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.schema import RawEmail as SchemaRawEmail
from models.schema import ReadStatus

from ingestion import parse
from ingestion.tests import fixtures as fx


class TestHtmlToText:
    def test_strips_tags_and_unescapes_entities(self):
        text = parse.html_to_text("<p>Bye &amp; thanks</p>")
        assert text == "Bye & thanks"

    def test_drops_script_and_style_content(self):
        text = parse.html_to_text(
            "<style>p{color:red}</style><p>Visible</p><script>alert('x')</script>"
        )
        assert "color:red" not in text
        assert "alert" not in text
        assert text == "Visible"

    def test_normalizes_non_breaking_space(self):
        assert parse.html_to_text("<p>Hi&nbsp;there</p>") == "Hi there"

    def test_block_tags_become_line_breaks(self):
        text = parse.html_to_text("<div>one</div><div>two</div>")
        assert text.splitlines() == ["one", "", "two"]

    def test_tolerates_malformed_markup(self):
        assert "content" in parse.html_to_text("<div><p>content</div></p><b>")

    def test_empty_input(self):
        assert parse.html_to_text("") == ""


class TestDecodeBase64Url:
    def test_handles_missing_padding(self):
        assert parse.decode_base64url(fx.b64("abcde")) == "abcde"

    def test_returns_empty_for_none_and_garbage(self):
        assert parse.decode_base64url(None) == ""
        assert parse.decode_base64url("!!!not base64!!!") == ""


class TestExtractBody:
    def test_plain_text_part(self):
        body = parse.extract_body(fx.PLAIN_ONLY)
        assert body == "Are you free Thursday at 1pm?\n\nDana"

    def test_html_only_falls_back_to_stripped_html(self):
        body = parse.extract_body(fx.HTML_ONLY)
        assert "<" not in body and ">" not in body
        assert "Hi there" in body
        assert "latest post & enjoy." in body
        assert "alert" not in body

    def test_multipart_alternative_prefers_plain(self):
        assert parse.extract_body(fx.MULTIPART_ALTERNATIVE) == "The plain version."

    def test_walks_nested_multiparts(self):
        assert parse.extract_body(fx.NESTED_WITH_ATTACHMENT) == "See attached."

    def test_attachment_only_message_has_empty_body(self):
        # Must return "" rather than raising — Phase 8 hardens downstream use.
        assert parse.extract_body(fx.ATTACHMENT_ONLY) == ""

    def test_attached_text_file_is_not_treated_as_body(self):
        assert parse.extract_body(fx.ATTACHED_TEXT_FILE) == ""

    def test_empty_payload(self):
        assert parse.extract_body({}) == ""


class TestHasAttachments:
    def test_true_for_real_attachment(self):
        assert parse.has_attachments(fx.NESTED_WITH_ATTACHMENT) is True

    def test_false_for_body_only(self):
        assert parse.has_attachments(fx.MULTIPART_ALTERNATIVE) is False


class TestExtractHeaders:
    def test_keeps_no_reply_detection_headers_verbatim(self):
        headers = parse.extract_headers(fx.HTML_ONLY)
        assert headers["Precedence"] == "bulk"
        assert headers["List-Unsubscribe"] == (
            "<https://acme.example/u/1>, <mailto:u@acme.example>"
        )

    def test_drops_headers_outside_the_allowlist(self):
        headers = parse.extract_headers(fx.ENCODED_HEADERS)
        assert "X-Mailer" not in headers

    def test_matches_case_insensitively_but_stores_canonical_name(self):
        # The fixture spells it "precedence"; downstream must find "Precedence".
        headers = parse.extract_headers(fx.ENCODED_HEADERS)
        assert headers["Precedence"] == "list"

    def test_decodes_rfc2047_encoded_words(self):
        headers = parse.extract_headers(fx.ENCODED_HEADERS)
        assert headers["From"] == "Jörg Müller <joerg@example.de>"

    def test_subject_is_not_duplicated_into_the_header_blob(self):
        # Subject has a dedicated RawEmail field; storing it twice would give
        # downstream tracks two sources of truth for the same value.
        assert "Subject" not in parse.extract_headers(fx.ENCODED_HEADERS)
        assert parse.get_header(fx.ENCODED_HEADERS, "Subject") == "Für dich"


class TestInternalDate:
    def test_converts_epoch_millis_to_aware_utc_datetime(self):
        result = parse.internal_date_to_datetime("1755864000000")
        assert result == datetime(2025, 8, 22, 12, 0, tzinfo=timezone.utc)
        assert result.tzinfo is not None

    def test_falls_back_to_now_when_missing(self):
        """The contract requires a datetime; a missing internalDate must not
        produce a None the rest of the pipeline has to defend against."""
        result = parse.internal_date_to_datetime(None)
        assert isinstance(result, datetime)
        assert result.utcoffset() == timedelta(0)


class TestToRawEmail:
    def test_maps_a_full_message(self):
        msg = fx.message(
            msg_id="abc", thread_id="thr", label_ids=["INBOX", "UNREAD"],
            snippet="Are you free", payload=fx.PLAIN_ONLY,
        )
        email = parse.to_raw_email(msg)
        assert email.email_id == "abc"
        assert email.thread_id == "thr"
        assert email.sender == "Dana Reed <dana@example.com>"
        assert email.subject == "Lunch Thursday?"
        assert email.read_status == ReadStatus.UNREAD
        assert email.received_at == datetime(2025, 8, 22, 12, 0, tzinfo=timezone.utc)
        assert email.has_attachments is False
        # Populated from To:/Cc: — required by the frozen contract and by
        # Track B's direct-vs-CC importance signal.
        assert email.recipients == ["me@example.com"]

    def test_is_the_frozen_contract_type(self):
        """Track B types against models.schema.RawEmail; ingestion output must
        satisfy isinstance, not merely resemble it. This is the Checkpoint 1
        integration bug that used to live here."""
        email = parse.to_raw_email(fx.message(payload=fx.PLAIN_ONLY))
        assert isinstance(email, SchemaRawEmail)

    def test_recipients_include_cc(self):
        payload = dict(fx.PLAIN_ONLY)
        payload["headers"] = fx.headers(
            From="Dana Reed <dana@example.com>",
            To="me@example.com",
            Cc="team@example.com, lead@example.com",
            Subject="Lunch Thursday?",
        )
        email = parse.to_raw_email(fx.message(payload=payload))
        assert email.recipients == [
            "me@example.com", "team@example.com", "lead@example.com"
        ]

    def test_read_status_is_read_without_the_unread_label(self):
        msg = fx.message(label_ids=["INBOX"], payload=fx.PLAIN_ONLY)
        assert parse.to_raw_email(msg).read_status == ReadStatus.READ

    def test_hint_headers_surface_automation_signals(self):
        msg = fx.message(payload=fx.HTML_ONLY)
        hints = parse.to_raw_email(msg).hint_headers()
        assert set(hints) == {"List-Unsubscribe", "Precedence"}


class TestPlainPartsThatArentPlain:
    """Regressions from real inbox data.

    7 of the first 100 real messages ingested had markup, stylesheets, or
    invisible padding surviving into `body_text` because the sender's
    `text/plain` part wasn't actually plain text.
    """

    def _body(self, plain_text):
        payload = {
            "mimeType": "text/plain",
            "headers": fx.headers(From="x@y.z", Subject="s"),
            "body": {"data": fx.b64(plain_text)},
        }
        return parse.extract_body(payload)

    def test_html_inside_a_plain_part_is_stripped(self):
        # Montana State: text/plain part opened with <div class="dynamic-content">
        body = self._body(
            '<div class="dynamic-content"><p style="font-size:16px">'
            "<span>Ronith, you have been selected</span></p></div>"
        )
        assert "<" not in body
        assert "font-size" not in body
        assert "Ronith, you have been selected" in body

    def test_bare_stylesheet_in_a_plain_part_is_removed(self):
        # College Board: raw CSS with no <style> tag to key off.
        body = self._body(
            "College Board\n"
            "body { margin: 0 !important; padding: 0 !important; width: 100% !important; }\n"
            "New sign-on detected."
        )
        assert "margin" not in body
        assert "!important" not in body
        assert "College Board" in body
        assert "New sign-on detected." in body

    def test_entities_in_genuine_plain_text_are_unescaped(self):
        assert self._body("Rock &amp; Roll") == "Rock & Roll"

    def test_invisible_preheader_padding_is_removed(self):
        # SDSU: runs of ZWNJ / figure space / soft hyphen / CGJ used as spacers.
        body = self._body(
            "See where access meets excellence.\n"
            "͏ ‌   ­ ͏ ‌   ­\n"
            "Real content here."
        )
        assert "‌" not in body and "­" not in body and "͏" not in body
        assert body == "See where access meets excellence.\n\nReal content here."

    def test_prose_containing_braces_is_not_mistaken_for_css(self):
        # The CSS matcher requires a colon inside the braces.
        text = "Use the {placeholder} token in your template."
        assert self._body(text) == text

    def test_genuine_plain_text_is_untouched(self):
        text = "Are you free Thursday at 1pm?\n\nDana"
        assert self._body(text) == text


class TestLooksLikeHtml:
    def test_detects_tags(self):
        assert parse.looks_like_html("<div>hi</div>") is True
        assert parse.looks_like_html("a <br/> b") is True

    def test_detects_bare_css_rules(self):
        assert parse.looks_like_html("body { margin: 0; }") is True

    def test_plain_prose_is_not_html(self):
        assert parse.looks_like_html("Meeting at 3pm. Cost < $50 > budget?") is False
