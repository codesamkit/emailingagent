"""Tests for context/chunk.py — quote stripping and chunking. No network, no model.

The assertion that matters most is negative: quoted reply history must not
appear in any kind=BODY chunk. Everything downstream — embeddings, entity
extraction, retrieval — inherits that failure silently if it regresses.
"""

from __future__ import annotations

import re
import unittest

from context.chunk import chunk_email, split_body, split_quoted, split_signature
from context.tests.fakes import raw
from models.schema import ChunkKind

PLAIN = """Hector,

The dock firmware question is mine to answer. Nobody owns it right now.

Dock 1.0 through 1.2 were written by Emil Brandt, who left in March 2025.
Dock 1.3 was a contractor build under a fixed-scope engagement.

I can take it on if we accept a slower cadence."""

# The real shape from the corpus: body, then a "--" signature, then an
# attribution line WRAPPED across two lines, then the quoted history.
REPLY_CHAIN = """Confirming the change worked. Last night's run cleared.

--
Aleksandra Petrova
Firmware Lead | StrideCore Technologies
s.petrova@stridecore.com | +1 503-555-0155

On Sat, Aug 22, 2026 11:54 PM, Inconspicuous Turtle <
boredomcure2020@gmail.com> wrote:

> Sasha, Hector, Ronith —
>
> Taking Sasha's question because it is mine to answer.
>
> On Fri, Aug 21, 2026 at 4:02 PM Hector Villalobos wrote:
>
> > Who maintains dock firmware? Nobody has answered this in two weeks.
"""

FORWARDED = """Passing this along, it changes the Meridian timeline.

-----Original Message-----
From: Priya Nandakumar <priya@nexusrecruit-global.io>
Sent: Thursday, August 20, 2026 2:14 PM
Subject: Confidential: VP Platform search

Ronith, we have a mandate that matches your profile at ticket SUP-2291.
"""

SIGNATURE_HEAVY = """Quick one: can you confirm the tranche 1 number before Friday?

Best,
Grant Feldman
Supply Chain Manager | StrideCore Technologies
g.feldman@stridecore.com | +1 503-555-0138

--
StrideCore Technologies | 4400 SW Macadam Ave, Portland OR 97239
You're receiving this because you are on the supply chain list.
Unsubscribe here and I won't email again."""

# A machine-generated notification that uses ">" as a block quote in the
# MIDDLE of its body, with real content underneath. Cutting at the first ">"
# would throw that content away; four messages in the real corpus look
# exactly like this.
BLOCK_QUOTE_MIDBODY = """PAGERDUTY — INCIDENT RESOLVED

Incident #4471
API p99 latency above threshold

RESOLUTION NOTE
> Raised payload rate limit for Meridian API key from 50 MB/min to
> 200 MB/min and concurrency from 8 to 16.
>
> This monitor has fired 11 times in 14 days for the same cause.

RELATED
This incident is 1 of 11 with matching signature in the last 14 days.
Consider creating a recurring-incident review.

TIMELINE
02:14 Triggered by Datadog monitor 8847221
02:41 Resolved by Devon Marsh
"""


def body_text(email_body: str, **kwargs) -> str:
    chunks = chunk_email(raw(body=email_body), **kwargs)
    return "\n\n".join(c.text for c in chunks if c.kind == ChunkKind.BODY)


def kinds(email_body: str) -> list:
    return [c.kind for c in chunk_email(raw(body=email_body))]


class SplitQuotedTest(unittest.TestCase):
    def test_plain_email_has_no_quoted_region(self):
        kept, quoted = split_quoted(PLAIN)
        self.assertEqual(quoted, "")
        self.assertEqual(kept, PLAIN)

    def test_wrapped_attribution_line_is_found(self):
        """The attribution wraps across two lines after whitespace
        normalization, so a line-anchored pattern would miss every one."""
        kept, quoted = split_quoted(REPLY_CHAIN)
        self.assertIn("Confirming the change worked", kept)
        self.assertNotIn("Sasha, Hector, Ronith", kept)
        self.assertTrue(quoted.startswith("On Sat, Aug 22"))

    def test_nested_reply_is_all_one_quoted_region(self):
        _, quoted = split_quoted(REPLY_CHAIN)
        self.assertIn("Taking Sasha's question", quoted)
        self.assertIn("Who maintains dock firmware", quoted)

    def test_original_message_divider(self):
        kept, quoted = split_quoted(FORWARDED)
        self.assertEqual(kept, "Passing this along, it changes the Meridian timeline.")
        self.assertIn("SUP-2291", quoted)

    def test_midbody_block_quote_is_kept(self):
        """">" is not proof of reply history. A run with real content after it
        is a block quote and must survive."""
        kept, quoted = split_quoted(BLOCK_QUOTE_MIDBODY)
        self.assertEqual(quoted, "")
        self.assertIn("Raised payload rate limit", kept)
        self.assertIn("recurring-incident review", kept)


class SplitSignatureTest(unittest.TestCase):
    def test_dash_delimiter_without_trailing_space(self):
        """ingestion.parse strips every line, so RFC "-- " arrives as "--".
        Anchoring on the space would miss 156 of 163 real messages."""
        kept, sig = split_signature("Body here.\n\n--\nGrant Feldman\n+1 503-555-0138")
        self.assertEqual(kept, "Body here.")
        self.assertIn("Grant Feldman", sig)

    def test_contact_block_without_a_delimiter(self):
        text = (
            "Can you confirm the number before Friday?\n\n"
            "Priya Nandakumar\nPartner, Technology Practice\n"
            "m: +1 415-555-0177 | priya@nexusrecruit-global.io"
        )
        kept, sig = split_signature(text)
        self.assertEqual(kept, "Can you confirm the number before Friday?")
        self.assertIn("415-555-0177", sig)

    def test_prose_paragraph_is_not_eaten(self):
        text = (
            "First point about the timeline.\n\n"
            "Second point: we should decide on the tranche size before "
            "Friday, because the freight booking closes then and Bram needs "
            "the number to sequence the sites."
        )
        kept, sig = split_signature(text)
        self.assertEqual(sig, "")
        self.assertEqual(kept, text)

    def test_signoff_above_the_delimiter_is_stripped_too(self):
        """A "--" footer often sits BELOW a hand-typed sign-off. Cutting only
        at the delimiter leaves a name, title, org, and phone number in a body
        chunk, to be embedded and mined as though the sender had said it."""
        kept, sig = split_signature(SIGNATURE_HEAVY)
        self.assertEqual(kept, "Quick one: can you confirm the tranche 1 number before Friday?")
        self.assertIn("Grant Feldman", sig)
        self.assertIn("503-555-0138", sig)
        self.assertIn("Macadam", sig)

    def test_horizontal_rule_far_from_the_end_is_not_a_signature(self):
        text = "Intro.\n\n--\n\n" + ("Body paragraph. " * 120)
        kept, sig = split_signature(text)
        self.assertEqual(sig, "")


class SplitBodyTest(unittest.TestCase):
    def test_respects_target_and_never_splits_mid_sentence(self):
        text = "\n\n".join("Sentence one here. Sentence two here." for _ in range(20))
        chunks = split_body(text, target_chars=200, overlap=40)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(
                chunk.rstrip().endswith((".", "!", "?", ":", ";")),
                "chunk ended mid-sentence: {0!r}".format(chunk[-60:]),
            )

    def test_overlap_carries_context_forward(self):
        text = "\n\n".join("Para {0} says something specific.".format(i) for i in range(12))
        with_overlap = split_body(text, target_chars=120, overlap=60)
        without = split_body(text, target_chars=120, overlap=0)
        self.assertGreater(
            sum(len(c) for c in with_overlap), sum(len(c) for c in without)
        )

    def test_single_oversized_sentence_stays_whole(self):
        long_sentence = "word " * 400
        self.assertEqual(len(split_body(long_sentence, target_chars=100)), 1)

    def test_empty_body(self):
        self.assertEqual(split_body("   \n\n  "), [])


class ChunkEmailTest(unittest.TestCase):
    def test_body_chunks_never_contain_quoted_text(self):
        for name, text in (
            ("reply chain", REPLY_CHAIN),
            ("forwarded", FORWARDED),
            ("signature heavy", SIGNATURE_HEAVY),
        ):
            with self.subTest(name):
                body = body_text(text)
                self.assertNotRegex(body, r"^>", "quote marker in body")
                self.assertNotIn("wrote:", body)

    def test_nothing_is_discarded(self):
        chunks = chunk_email(raw(body=REPLY_CHAIN))
        present = {c.kind for c in chunks}
        self.assertEqual(present, {ChunkKind.BODY, ChunkKind.SIGNATURE, ChunkKind.QUOTED})

    def test_signature_heavy_email_keeps_its_one_real_sentence(self):
        body = body_text(SIGNATURE_HEAVY)
        self.assertIn("confirm the tranche 1 number", body)
        self.assertNotIn("Unsubscribe", body)
        self.assertNotIn("Macadam", body)
        self.assertNotIn("503-555-0138", body)
        self.assertNotIn("Supply Chain Manager", body)

    def test_ids_are_deterministic_and_ordered(self):
        first = chunk_email(raw(body=REPLY_CHAIN))
        again = chunk_email(raw(body=REPLY_CHAIN))
        self.assertEqual([c.chunk_id for c in first], [c.chunk_id for c in again])
        self.assertEqual([c.ord for c in first], list(range(len(first))))
        self.assertTrue(all(c.chunk_id == "e1:{0}".format(c.ord) for c in first))

    def test_document_order_body_then_signature_then_quoted(self):
        order = [c.kind for c in chunk_email(raw(body=REPLY_CHAIN))]
        self.assertEqual(order.index(ChunkKind.BODY), 0)
        self.assertLess(order.index(ChunkKind.SIGNATURE), order.index(ChunkKind.QUOTED))

    def test_plain_email_is_body_only(self):
        self.assertEqual(set(kinds(PLAIN)), {ChunkKind.BODY})

    def test_empty_body_yields_no_chunks(self):
        self.assertEqual(chunk_email(raw(body="")), [])

    def test_attachment_only_email_does_not_raise(self):
        self.assertEqual(chunk_email(raw(body="   ")), [])


if __name__ == "__main__":
    unittest.main()
