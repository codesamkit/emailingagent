"""Tests for context/normalize.py — where over- and under-merging start.

Every entity identity in the graph is whatever this module returns, so its
failures are the silent kind: two spellings that should agree and do not, or
two different things folded onto one key. The cases below are the ones that
actually went wrong on real output.
"""

from __future__ import annotations

import unittest

from context.normalize import (
    normalize_address,
    normalize_id,
    normalize_name,
    parse_provisional,
    provisional_id,
)
from models.schema import EntityKind


class NormalizeNameTest(unittest.TestCase):
    def test_case_punctuation_and_whitespace_are_ignored(self):
        for variant in ("Atlas", "atlas", "  ATLAS  ", "Atlas.", "Atlas!!"):
            with self.subTest(variant):
                self.assertEqual(normalize_name(variant, EntityKind.PROJECT), "atlas")

    def test_leading_article_is_dropped(self):
        self.assertEqual(
            normalize_name("The Atlas", EntityKind.PROJECT), "atlas"
        )

    def test_the_generic_noun_people_attach_and_drop_is_removed(self):
        """"the Atlas project", "Project Atlas", and "Atlas" are one thing."""
        for variant in ("Atlas", "Project Atlas", "the Atlas project", "Atlas Programme"):
            with self.subTest(variant):
                self.assertEqual(normalize_name(variant, EntityKind.PROJECT), "atlas")

    def test_trailing_plural_is_folded(self):
        self.assertEqual(
            normalize_name("Sample Kits", EntityKind.DELIVERABLE),
            normalize_name("sample kit", EntityKind.DELIVERABLE),
        )

    def test_a_word_ending_in_s_is_not_a_plural(self):
        """"Atlas" normalizing to "atla" stops it matching itself — the exact
        silent failure this guard exists for. Over-merging is the primary risk
        (PHASES-COMPLEX.md §10), so the rule stays conservative."""
        for word in ("Atlas", "Vantera Analysis", "Bastion Campus", "Chaos"):
            with self.subTest(word):
                self.assertEqual(
                    normalize_name(word, EntityKind.PROJECT), word.lower()
                )

    def test_ies_folds_to_the_real_singular(self):
        """"Technologies" -> "technologie" would match neither "technology"
        nor anything else."""
        self.assertEqual(
            normalize_name("StrideCore Technologies", EntityKind.ORG),
            normalize_name("StrideCore Technology", EntityKind.ORG),
        )
        self.assertEqual(
            normalize_name("StrideCore Technologies", EntityKind.ORG),
            "stridecore technology",
        )

    def test_person_names_are_never_singularized(self):
        """A PERSON keys on an address, and folding a trailing "s" off a name
        would merge two people."""
        self.assertEqual(
            normalize_name("Jonas", EntityKind.PERSON), "jonas"
        )

    def test_no_kind_means_no_singularization(self):
        self.assertEqual(normalize_name("Sample Kits"), "sample kits")

    def test_addresses_survive_intact(self):
        self.assertEqual(
            normalize_name("h.villalobos@stridecore.com", EntityKind.PERSON),
            "h.villalobos@stridecore.com",
        )

    def test_empty_input(self):
        self.assertEqual(normalize_name(""), "")
        self.assertEqual(normalize_name(None), "")

    def test_a_name_made_only_of_generic_words_is_not_erased(self):
        self.assertTrue(normalize_name("The Project", EntityKind.PROJECT))


class NormalizeIdTest(unittest.TestCase):
    def test_separators_are_ignored(self):
        for variant in ("CS-40350", "cs 40350", "CS40350", "cs/40350"):
            with self.subTest(variant):
                self.assertEqual(normalize_id(variant), "CS40350")

    def test_multipart_ids_keep_every_part(self):
        """Folding RMA-2026-0447 down to RMA2026 would collide with every
        other 2026-dated RMA."""
        self.assertEqual(normalize_id("RMA-2026-0447"), "RMA20260447")
        self.assertNotEqual(normalize_id("RMA-2026-0447"), normalize_id("RMA-2026-0448"))

    def test_hash_form(self):
        self.assertEqual(normalize_id("#4471"), "4471")

    def test_never_singularized(self):
        self.assertEqual(normalize_id("SC-400s"), "SC400S")

    def test_empty(self):
        self.assertEqual(normalize_id(""), "")


class NormalizeAddressTest(unittest.TestCase):
    def test_display_name_form(self):
        self.assertEqual(
            normalize_address("Hector Villalobos <H.Villalobos@StrideCore.com>"),
            "h.villalobos@stridecore.com",
        )

    def test_bare_address(self):
        self.assertEqual(normalize_address("  A@B.COM "), "a@b.com")

    def test_reuses_the_existing_parser(self):
        """One address parser in the repo, not two."""
        from scoring.signals import _addr_only

        value = "Anna <a@b.com>"
        self.assertEqual(normalize_address(value), _addr_only(value))


class ProvisionalIdTest(unittest.TestCase):
    def test_round_trip(self):
        for kind in EntityKind:
            with self.subTest(kind):
                self.assertEqual(
                    parse_provisional(provisional_id(kind, "key")), (kind, "key")
                )

    def test_a_real_entity_id_is_not_provisional(self):
        """A real id is opaque and has no ":", so this doubles as the test for
        "has this mention been resolved yet"."""
        self.assertIsNone(parse_provisional("deadbeefdeadbeef"))

    def test_an_unknown_kind_prefix_is_rejected(self):
        self.assertIsNone(parse_provisional("nonsense:key"))

    def test_a_key_containing_a_colon_keeps_its_tail(self):
        self.assertEqual(
            parse_provisional(provisional_id(EntityKind.TOPIC, "a:b")),
            (EntityKind.TOPIC, "a:b"),
        )

    def test_empty_id(self):
        self.assertIsNone(parse_provisional(""))
        self.assertIsNone(parse_provisional(None))


if __name__ == "__main__":
    unittest.main()
