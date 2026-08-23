"""Tests for context/extract.py. No network.

The deterministic pass is tested with an ExplodingClient, which fails the test
if any model call is attempted — the same posture as
drafting/tests/test_outline_gating.py asserting the client is never invoked
rather than only asserting the output. Pass 2 runs against a scripted client.
"""

from __future__ import annotations

import unittest

from context.chunk import chunk_email
from context.extract import (
    FREEMAIL_DOMAINS,
    extract_deterministic,
    extract_entities,
    find_addresses,
    find_ids,
    strip_reply_prefixes,
)
from context.tests.fakes import ExplodingClient, FakeClient, raw
from models.schema import Chunk, ChunkKind, EntityKind, MentionSource


def body_chunk(text: str, chunk_id: str = "e1:0") -> Chunk:
    return Chunk(chunk_id=chunk_id, email_id="e1", ord=0, text=text, kind=ChunkKind.BODY)


def keys(mentions, kind: EntityKind):
    prefix = kind.value + ":"
    return sorted(m.entity_id[len(prefix) :] for m in mentions if m.entity_id.startswith(prefix))


def llm_payload(**overrides):
    payload = {
        "reason": "The email is about the Vantera RMA.",
        "projects": [],
        "deliverables": [],
        "topics": [],
        "primary_ids": [],
        "case_ids": [],
    }
    payload.update(overrides)
    return payload


class IdPatternTest(unittest.TestCase):
    def test_dashed_and_hash_forms(self):
        found = dict(
            (token, kind) for token, kind in find_ids("CS-40350 and #4471 and INV-2201")
        )
        self.assertEqual(found["CS-40350"], EntityKind.CASE)
        self.assertEqual(found["#4471"], EntityKind.CASE)
        self.assertEqual(found["INV-2201"], EntityKind.DOCUMENT)

    def test_multipart_id_is_not_truncated(self):
        """RMA-2026-0447 truncated to RMA-2026 would collide with every other
        2026-dated RMA in the corpus."""
        self.assertEqual([t for t, _ in find_ids("RMA-2026-0447 raised")], ["RMA-2026-0447"])

    def test_document_prefixes(self):
        for token in ("INV-8891", "PO-4501", "ORD-77", "RMA-1"):
            with self.subTest(token):
                self.assertEqual(find_ids(token)[0][1], EntityKind.DOCUMENT)

    def test_unknown_prefix_defaults_to_document_not_case(self):
        """Chosen from real output. Defaulting to CASE produced 68 CASE nodes
        of which 35 appeared in one email, because serial numbers, part
        numbers, and account numbers all match the same shape as a ticket id.
        DOCUMENT is the honest default; pass 2 promotes the real cases."""
        for token in ("SN-4400-2283", "ACT-9916", "MAT-221", "XX-9"):
            with self.subTest(token):
                self.assertEqual(find_ids(token)[0][1], EntityKind.DOCUMENT)

    def test_short_hash_numbers_in_prose_are_not_ids(self):
        self.assertEqual(find_ids("item #3 and #22 of the list"), [])

    def test_deduplicated_by_normalized_form(self):
        self.assertEqual(len(find_ids("CS-40350, cs 40350, CS-40350")), 1)


class SubjectPrefixTest(unittest.TestCase):
    def test_ids_past_stacked_reply_prefixes(self):
        """84 of 163 real subjects carry two or more prefixes, so a scan that
        stops at the start of the string finds nothing."""
        subject = "Re: Re: Re: [support@stridecore.com] [CS-40350] RMA authorization"
        self.assertTrue(strip_reply_prefixes(subject).startswith("[support@"))
        self.assertIn("CS-40350", [t for t, _ in find_ids(strip_reply_prefixes(subject))])

    def test_fwd_and_mixed_case(self):
        self.assertEqual(strip_reply_prefixes("FWD: re: Fw: Hello"), "Hello")


class DeterministicPassTest(unittest.TestCase):
    def test_makes_no_model_call(self):
        extract_deterministic(
            raw(subject="[CS-40350] hello", body="body"), [body_chunk("CS-40350 update")]
        )  # would raise if a client were touched

    def test_person_keys_on_the_address_not_the_display_name(self):
        """The same human arrives as "Sam", "Sam Shah", and "S. Shah"; keying
        on any spelling makes three nodes for one person."""
        mentions = extract_deterministic(
            raw(sender="S. Shah <sam.shah@meridianvc-partners.com>", recipients=[]),
        )
        self.assertEqual(keys(mentions, EntityKind.PERSON), ["sam.shah@meridianvc-partners.com"])
        person = next(m for m in mentions if m.entity_id.startswith("person:"))
        self.assertEqual(person.span_text, "S. Shah")
        self.assertEqual(person.source, MentionSource.HEADER)

    def test_org_from_work_domain(self):
        mentions = extract_deterministic(
            raw(sender="a@stridecore.com", recipients=[]),
        )
        self.assertEqual(keys(mentions, EntityKind.ORG), ["stridecore"])

    def test_freemail_domain_never_becomes_an_org(self):
        """An org node for gmail.com is 40 unrelated people in one bucket, and
        a hub every graph walk passes through."""
        for domain in ("gmail.com", "outlook.com", "icloud.com", "proton.me"):
            with self.subTest(domain):
                mentions = extract_deterministic(
                    raw(sender="someone@" + domain, recipients=[])
                )
                self.assertEqual(keys(mentions, EntityKind.ORG), [])
        self.assertIn("yahoo.com", FREEMAIL_DOMAINS)

    def test_cc_and_reply_to_headers_are_read(self):
        mentions = extract_deterministic(
            raw(
                sender="a@stridecore.com",
                recipients=["me@example.com"],
                headers={
                    "Cc": "Bram Kuiper <b.kuiper@vantera.nl>, g.feldman@stridecore.com",
                    "Reply-To": "support@stridecore.com",
                },
            )
        )
        people = keys(mentions, EntityKind.PERSON)
        self.assertIn("b.kuiper@vantera.nl", people)
        self.assertIn("g.feldman@stridecore.com", people)
        self.assertIn("support@stridecore.com", people)
        self.assertIn("vantera", keys(mentions, EntityKind.ORG))

    def test_address_written_into_the_subject_is_extracted(self):
        """Every message in this corpus arrives through one relay account, so
        the From header is the same string 161 times and the real
        correspondent is in the subject. Header-only extraction yields one
        PERSON node for the whole corpus."""
        mentions = extract_deterministic(
            raw(
                sender="Relay <relay@gmail.com>",
                recipients=["me@example.com"],
                subject="Re: [h.villalobos@stridecore.com] [CS-40377] Bastion",
            )
        )
        self.assertIn("h.villalobos@stridecore.com", keys(mentions, EntityKind.PERSON))
        self.assertIn("stridecore", keys(mentions, EntityKind.ORG))

    def test_quoted_and_signature_chunks_never_yield_mentions(self):
        """An id inside quoted history belongs to whoever wrote the original
        message, not to whoever replied below it."""
        chunks = [
            body_chunk("Confirming the change worked.", "e1:0"),
            Chunk(chunk_id="e1:1", email_id="e1", ord=1,
                  text="ticket CS-99999 from the footer", kind=ChunkKind.SIGNATURE),
            Chunk(chunk_id="e1:2", email_id="e1", ord=2,
                  text="> the original was about CS-11111", kind=ChunkKind.QUOTED),
        ]
        mentions = extract_deterministic(raw(subject="no ids here"), chunks)
        self.assertEqual(keys(mentions, EntityKind.CASE), [])

    def test_body_ids_carry_their_chunk_id(self):
        mentions = extract_deterministic(
            raw(subject="no ids"), [body_chunk("please see CS-40350", "e1:7")]
        )
        case = next(m for m in mentions if m.entity_id == "case:CS40350")
        self.assertEqual(case.chunk_id, "e1:7")

    def test_subject_ids_have_no_chunk_id(self):
        mentions = extract_deterministic(raw(subject="[CS-40350] hi"), [])
        case = next(m for m in mentions if m.entity_id == "case:CS40350")
        self.assertIsNone(case.chunk_id)


class AddressScanTest(unittest.TestCase):
    def test_finds_and_lowercases(self):
        self.assertEqual(
            find_addresses("Ask A.Kovacs@MeridianVC-Partners.com about it."),
            ["a.kovacs@meridianvc-partners.com"],
        )

    def test_trailing_period_is_not_part_of_the_address(self):
        self.assertEqual(find_addresses("mail me@x.com."), ["me@x.com"])


class LlmPassTest(unittest.TestCase):
    def test_no_body_text_means_no_model_call(self):
        mentions = extract_entities(
            raw(subject="[CS-40350] hi", body=""), [], client=ExplodingClient()
        )
        self.assertTrue(mentions)
        self.assertTrue(all(m.source != MentionSource.LLM for m in mentions))

    def test_projects_deliverables_and_topics_become_mentions(self):
        client = FakeClient(
            llm_payload(
                projects=["Vantera Safety Group rollout"],
                deliverables=["Sample kits"],
                topics=["rma authorization"],
                confidence=0.9,
            )
        )
        mentions = extract_entities(
            raw(subject="[CS-40350] RMA"), [body_chunk("We need the RMA authorized.")],
            client=client,
        )
        self.assertEqual(client.call_count, 1, "pass 2 must be exactly ONE call")
        self.assertEqual(keys(mentions, EntityKind.PROJECT), ["vantera safety group rollout"])
        self.assertEqual(keys(mentions, EntityKind.DELIVERABLE), ["sample kit"])
        self.assertEqual(keys(mentions, EntityKind.TOPIC), ["rma authorization"])
        for mention in mentions:
            if mention.source == MentionSource.LLM:
                self.assertEqual(mention.confidence, 0.9)

    def test_reason_is_declared_before_the_answers(self):
        """Constrained decoding emits fields in declaration order, so reason
        first informs the answer instead of rationalizing it."""
        from context.extract import RESPONSE_SCHEMA

        fields = list(RESPONSE_SCHEMA["properties"])
        self.assertEqual(fields[0], "reason")

    def test_every_schema_string_has_a_max_length(self):
        from context.extract import RESPONSE_SCHEMA

        for name, spec in RESPONSE_SCHEMA["properties"].items():
            with self.subTest(name):
                target = spec.get("items", spec)
                if target.get("type") == "string":
                    self.assertIn("maxLength", target)

    def test_the_schema_declares_no_maxitems(self):
        """Valid JSON Schema, honoured by ollama's constrained decoder, and
        rejected outright by the Anthropic structured-output API:
        400 "For 'array' type, property 'maxItems' is not supported".
        The bound lives in MAX_ITEMS and is applied in code instead."""
        from context.extract import MAX_ITEMS, RESPONSE_SCHEMA

        for name, spec in RESPONSE_SCHEMA["properties"].items():
            with self.subTest(name):
                self.assertNotIn("maxItems", spec)
                if spec.get("type") == "array":
                    self.assertIn(name, MAX_ITEMS)

    def test_an_overlong_array_is_truncated_in_code(self):
        from context.extract import MAX_ITEMS

        cap = MAX_ITEMS["projects"]
        client = FakeClient(
            llm_payload(projects=["Project {0}".format(i) for i in range(cap + 5)])
        )
        mentions = extract_entities(raw(), [body_chunk("text")], client=client)
        self.assertEqual(len(keys(mentions, EntityKind.PROJECT)), cap)

    def test_a_non_list_array_field_does_not_raise(self):
        client = FakeClient(llm_payload(projects="not a list", primary_ids=None))
        mentions = extract_entities(raw(), [body_chunk("text")], client=client)
        self.assertEqual(keys(mentions, EntityKind.PROJECT), [])

    def test_model_is_not_shown_quoted_or_signature_text(self):
        client = FakeClient(llm_payload())
        chunks = [
            body_chunk("The real ask is here.", "e1:0"),
            Chunk(chunk_id="e1:1", email_id="e1", ord=1,
                  text="SECRETQUOTEDTEXT", kind=ChunkKind.QUOTED),
        ]
        extract_entities(raw(), chunks, client=client)
        self.assertIn("The real ask is here.", client.last_user_message)
        self.assertNotIn("SECRETQUOTEDTEXT", client.last_user_message)

    def test_model_is_told_which_ids_came_from_the_subject(self):
        client = FakeClient(llm_payload())
        extract_entities(
            raw(subject="[CS-40350] RMA"), [body_chunk("also see CS-11111")], client=client
        )
        message = client.last_user_message
        self.assertIn("CS-40350 (in subject)", message)
        self.assertIn("CS-11111", message)
        self.assertNotIn("CS-11111 (in subject)", message)

    def test_incidental_ids_are_downweighted_not_dropped(self):
        client = FakeClient(llm_payload(primary_ids=["CS-40350"]))
        mentions = extract_entities(
            raw(subject="[CS-40350] RMA"),
            [body_chunk("Unrelated older ticket CS-11111 for reference.")],
            client=client,
        )
        by_id = {m.entity_id: m for m in mentions if m.entity_id.startswith("case:")}
        self.assertEqual(by_id["case:CS40350"].confidence, 1.0)
        self.assertIn("case:CS11111", by_id, "an incidental id is still a real mention")
        self.assertLess(by_id["case:CS11111"].confidence, 1.0)

    def test_case_ids_promote_an_unknown_prefix_to_case(self):
        client = FakeClient(
            llm_payload(primary_ids=["ACT-9916"], case_ids=["ACT-9916"])
        )
        mentions = extract_entities(
            raw(subject="ACT-9916 escalation"), [body_chunk("please action ACT-9916")],
            client=client,
        )
        self.assertIn("ACT9916", keys(mentions, EntityKind.CASE))
        self.assertEqual(keys(mentions, EntityKind.DOCUMENT), [])

    def test_an_unpromoted_unknown_prefix_stays_a_document(self):
        client = FakeClient(llm_payload(primary_ids=["SN-4400-2283"], case_ids=[]))
        mentions = extract_entities(
            raw(subject="dock unit"), [body_chunk("unit SN-4400-2283 failed")],
            client=client,
        )
        self.assertIn("SN44002283", keys(mentions, EntityKind.DOCUMENT))
        self.assertEqual(keys(mentions, EntityKind.CASE), [])

    def test_a_known_invoice_prefix_cannot_be_promoted(self):
        """This corrects the default; it does not let a model relitigate a
        convention it has no better information about."""
        client = FakeClient(llm_payload(primary_ids=["INV-8891"], case_ids=["INV-8891"]))
        mentions = extract_entities(
            raw(subject="INV-8891"), [body_chunk("invoice INV-8891 overdue")],
            client=client,
        )
        self.assertIn("INV8891", keys(mentions, EntityKind.DOCUMENT))
        self.assertEqual(keys(mentions, EntityKind.CASE), [])

    def test_a_known_case_prefix_is_never_demoted(self):
        client = FakeClient(llm_payload(primary_ids=["CS-40350"], case_ids=[]))
        mentions = extract_entities(
            raw(subject="[CS-40350] RMA"), [body_chunk("about CS-40350")], client=client
        )
        self.assertIn("CS40350", keys(mentions, EntityKind.CASE))

    def test_the_prompt_defines_case_against_reference(self):
        from context.extract import SYSTEM_PROMPT

        self.assertIn("serial number", SYSTEM_PROMPT.lower())
        self.assertIn("unit of work", SYSTEM_PROMPT.lower())

    def test_model_returning_an_id_or_address_is_ignored(self):
        """Those are already extracted exactly; a second spelling of the same
        thing is how one entity becomes two."""
        client = FakeClient(
            llm_payload(projects=["CS-40350", "a.kovacs@meridianvc-partners.com", "Atlas"])
        )
        mentions = extract_entities(raw(), [body_chunk("text")], client=client)
        self.assertEqual(keys(mentions, EntityKind.PROJECT), ["atlas"])

    def test_blank_and_duplicate_spans_are_dropped(self):
        client = FakeClient(llm_payload(projects=["Atlas", "  ", "the atlas project"]))
        mentions = extract_entities(raw(), [body_chunk("text")], client=client)
        self.assertEqual(keys(mentions, EntityKind.PROJECT), ["atlas"])

    def test_missing_confidence_falls_back_to_a_default(self):
        client = FakeClient(llm_payload(projects=["Atlas"]))
        mentions = extract_entities(raw(), [body_chunk("text")], client=client)
        project = next(m for m in mentions if m.entity_id.startswith("project:"))
        self.assertGreater(project.confidence, 0.0)
        self.assertLessEqual(project.confidence, 1.0)

    def test_deterministic_mentions_survive_the_llm_pass(self):
        client = FakeClient(llm_payload())
        mentions = extract_entities(
            raw(subject="[CS-40350] hi", sender="a@stridecore.com"),
            [body_chunk("text")],
            client=client,
        )
        self.assertIn("case:CS40350", {m.entity_id for m in mentions})
        self.assertIn("org:stridecore", {m.entity_id for m in mentions})


class Pass2FailureTest(unittest.TestCase):
    """A model failure must not cost an email the mentions that were free.

    Discarding pass 1 because pass 2 went wrong left 22 of 163 real emails with
    zero mentions — including the header and regex ones, which never needed a
    model and were never in doubt.
    """

    class _Broken:
        """A client whose response is unusable in a specific way."""

        def __init__(self, kind):
            self.kind = kind
            self.messages = self

        def create(self, **kwargs):
            if self.kind == "raises":
                raise RuntimeError("overloaded")
            return self

        @property
        def content(self):
            if self.kind == "truncated":
                return [_Block('{"reason":"the model ran out of bud')]
            if self.kind == "thinking_only":
                return [_Block(None, "thinking")]
            if self.kind == "not_an_object":
                return [_Block("[1, 2, 3]")]
            return []

        stop_reason = "max_tokens"

    def deterministic_survives(self, kind):
        mentions = extract_entities(
            raw(subject="[CS-40350] RMA", sender="a@stridecore.com"),
            [body_chunk("real body text")],
            client=self._Broken(kind),
        )
        ids = {m.entity_id for m in mentions}
        self.assertIn("case:CS40350", ids)
        self.assertIn("org:stridecore", ids)
        self.assertTrue(all(m.source != MentionSource.LLM for m in mentions))

    def test_truncated_json(self):
        self.deterministic_survives("truncated")

    def test_a_response_with_only_a_thinking_block(self):
        """A bare next() over the content blocks raises StopIteration, whose
        str() is empty — which is how 14 of these logged no reason at all."""
        self.deterministic_survives("thinking_only")

    def test_an_api_error(self):
        self.deterministic_survives("raises")

    def test_json_that_is_not_an_object(self):
        self.deterministic_survives("not_an_object")

    def test_no_content_blocks(self):
        self.deterministic_survives("empty")

    def test_the_output_budget_leaves_room_for_a_thinking_block(self):
        """A reasoning model's thinking is charged against max_tokens. At 700
        the budget ran out partway through the JSON on 32 of 163 emails."""
        from context.extract import MAX_OUTPUT_TOKENS

        self.assertGreaterEqual(MAX_OUTPUT_TOKENS, 2000)


class _Block:
    def __init__(self, text, type="text"):
        self.type = type
        self.text = text


class RealShapeTest(unittest.TestCase):
    """One end-to-end pass over a message shaped like the real corpus."""

    BODY = """Ingrid,

Recording the approval on CS-40350. Tranche 1 is 180 units.

--
Grant Feldman
Supply Chain Manager | StrideCore Technologies
g.feldman@stridecore.com | +1 503-555-0138

On Sat, Aug 22, 2026 11:54 PM, Relay <relay@gmail.com> wrote:

> The older ticket was CS-11111 and should be ignored.
"""

    def test_quoted_case_id_does_not_become_a_mention(self):
        email = raw(
            sender="Relay <relay@gmail.com>",
            subject="Re: Re: [g.feldman@stridecore.com] [CS-40350] Vantera RMA",
            body=self.BODY,
        )
        mentions = extract_deterministic(email, chunk_email(email))
        cases = keys(mentions, EntityKind.CASE)
        self.assertIn("CS40350", cases)
        self.assertNotIn("CS11111", cases)

    def test_signature_contact_details_do_not_become_mentions(self):
        email = raw(sender="Relay <relay@gmail.com>", subject="no ids", body=self.BODY)
        chunks = chunk_email(email)
        body_only = [c for c in chunks if c.kind == ChunkKind.BODY]
        self.assertTrue(all("503-555-0138" not in c.text for c in body_only))


if __name__ == "__main__":
    unittest.main()
