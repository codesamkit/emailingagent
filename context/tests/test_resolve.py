"""Tests for context/resolve.py — every rung of the ladder, and the edges.

No DB, no model, no clock: `resolve` takes embeddings as an argument precisely
so the 0.86 threshold can be tuned here, against hand-built 4-dimensional
vectors whose similarity is obvious by inspection, instead of against a live
ollama and a 160-email corpus.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from context.resolve import (
    DEFAULT_THRESHOLD,
    EntityIndex,
    entity_id_for,
    resolve,
)
from context.tests.fakes import vec
from models.schema import Entity, EntityKind, Mention, MentionSource


def mention(entity_id: str, span: str, email_id: str = "e1", **kwargs) -> Mention:
    return Mention(
        email_id=email_id,
        entity_id=entity_id,
        span_text=span,
        source=kwargs.pop("source", MentionSource.LLM),
        **kwargs
    )


def entity(kind: EntityKind, key: str, name: str, aliases=()) -> Entity:
    return Entity(
        entity_id=entity_id_for(kind, key),
        kind=kind,
        canonical_name=name,
        normalized_key=key,
        aliases=list(aliases),
    )


class Rung1ExactKeyTest(unittest.TestCase):
    def test_exact_key_matches_an_existing_entity(self):
        atlas = entity(EntityKind.PROJECT, "atlas", "Atlas")
        result = resolve([mention("project:atlas", "Atlas")], EntityIndex.build([atlas]))
        self.assertEqual(result.by_rung["exact"], 1)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.mentions[0].entity_id, atlas.entity_id)

    def test_same_name_different_kind_must_not_merge(self):
        """A PERSON called "Atlas" and a PROJECT called "Atlas" are two
        things. The key is scoped by kind for exactly this."""
        person = entity(EntityKind.PERSON, "atlas@x.com", "Atlas")
        project_person = entity(EntityKind.PERSON, "atlas", "Atlas")
        index = EntityIndex.build([person, project_person])
        result = resolve([mention("project:atlas", "Atlas")], index)
        self.assertEqual(result.created, 1)
        self.assertNotIn(
            result.mentions[0].entity_id, {person.entity_id, project_person.entity_id}
        )

    def test_two_spellings_in_one_batch_share_one_new_entity(self):
        result = resolve(
            [
                mention("project:atlas", "Atlas"),
                mention("project:atlas", "the Atlas project", "e2"),
            ]
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(len(result.entities), 1)
        self.assertEqual(result.entities[0].mention_count, 2)
        self.assertEqual(
            result.mentions[0].entity_id, result.mentions[1].entity_id
        )


class Rung2AliasTest(unittest.TestCase):
    def test_alias_matches(self):
        atlas = entity(
            EntityKind.PROJECT, "atlas program", "Atlas Program", aliases=["Atlas"]
        )
        result = resolve([mention("project:atlas", "Atlas")], EntityIndex.build([atlas]))
        self.assertEqual(result.by_rung["alias"], 1)
        self.assertEqual(result.mentions[0].entity_id, atlas.entity_id)

    def test_alias_is_scoped_by_kind(self):
        atlas = entity(
            EntityKind.PROJECT, "atlas program", "Atlas Program", aliases=["Atlas"]
        )
        result = resolve(
            [mention("deliverable:atlas", "Atlas")], EntityIndex.build([atlas])
        )
        self.assertEqual(result.created, 1)
        self.assertNotEqual(result.mentions[0].entity_id, atlas.entity_id)

    def test_alias_collision_resolves_to_the_first_claimant(self):
        """Two entities can legitimately claim one alias. First writer wins,
        deterministically — the alternative is a nondeterministic graph."""
        first = entity(EntityKind.PROJECT, "atlas program", "Atlas Program", ["Atlas"])
        second = entity(EntityKind.PROJECT, "atlas rollout", "Atlas Rollout", ["Atlas"])
        index = EntityIndex.build([first, second])
        result = resolve([mention("project:atlas", "Atlas")], index)
        self.assertEqual(result.mentions[0].entity_id, first.entity_id)

    def test_a_new_surface_form_is_recorded_as_an_alias(self):
        """A spelling that normalizes to a DIFFERENT key needs an alias row,
        because rung 1 will not find it next time."""
        result = resolve(
            [
                mention("project:atlas", "Atlas"),
                mention("project:atlas", "Bastion dock rollout", "e2"),
            ]
        )
        self.assertIn("Bastion dock rollout", result.entities[0].aliases)
        self.assertEqual(result.aliases, [(result.entities[0].entity_id, "Bastion dock rollout")])

    def test_a_surface_form_that_normalizes_to_the_key_gets_no_alias_row(self):
        """"Atlas Programme" normalizes to "atlas" — the generic noun is
        dropped — so rung 1 already catches it and an alias row is dead weight."""
        result = resolve(
            [
                mention("project:atlas", "Atlas"),
                mention("project:atlas", "Atlas Programme", "e2"),
            ]
        )
        self.assertIn("Atlas Programme", result.entities[0].aliases)
        self.assertEqual(result.aliases, [])

    def test_id_surface_forms_are_not_stored_as_aliases(self):
        """"CS-40350" is already the key modulo separators; an alias row for it
        can never match anything the key would not."""
        result = resolve([mention("case:CS40350", "CS-40350")])
        self.assertEqual(result.aliases, [])


class Rung3EmbeddingTest(unittest.TestCase):
    """The only rung that can wrongly fuse two entities, so it is the only one
    with a tunable number and it runs last."""

    def setUp(self):
        self.henderson = entity(
            EntityKind.PROJECT, "henderson escalation", "Henderson escalation"
        )
        # Cosine with [1,0,0,0]: identical=1.0, near=0.9805, far=0.4472.
        self.index = EntityIndex.build(
            [self.henderson], vectors={self.henderson.entity_id: vec(1, 0, 0, 0)}
        )

    def test_above_threshold_merges(self):
        result = resolve(
            [mention("project:henderson issue", "Henderson issue")],
            self.index,
            embeddings={"project:henderson issue": vec(1, 0.2, 0, 0)},
        )
        self.assertEqual(result.by_rung["vector"], 1)
        self.assertEqual(result.merged, 1)
        self.assertEqual(result.mentions[0].entity_id, self.henderson.entity_id)

    def test_below_threshold_creates(self):
        result = resolve(
            [mention("project:meridian escalation", "Meridian escalation")],
            self.index,
            embeddings={"project:meridian escalation": vec(1, 2, 0, 0)},
        )
        self.assertEqual(result.by_rung["vector"], 0)
        self.assertEqual(result.created, 1)

    def test_threshold_is_the_boundary_and_is_a_parameter(self):
        args = dict(
            existing=self.index,
            embeddings={"project:x": vec(1, 0.6, 0, 0)},   # cosine ~= 0.858
        )
        loose = resolve([mention("project:x", "X")], threshold=0.80, **args)
        tight = resolve([mention("project:x", "X")], threshold=0.90, **args)
        self.assertEqual(loose.merged, 1)
        self.assertEqual(tight.merged, 0)
        self.assertAlmostEqual(DEFAULT_THRESHOLD, 0.86, places=2)

    def test_ids_are_never_merged_by_embedding(self):
        """"CS-40350" and "CS-40351" embed almost identically and are two
        different cases. No amount of similarity may fuse them."""
        case = entity(EntityKind.CASE, "CS40350", "CS-40350")
        index = EntityIndex.build([case], vectors={case.entity_id: vec(1, 0, 0, 0)})
        result = resolve(
            [mention("case:CS40351", "CS-40351")],
            index,
            embeddings={"case:CS40351": vec(1, 0.001, 0, 0)},
        )
        self.assertEqual(result.merged, 0)
        self.assertNotEqual(result.mentions[0].entity_id, case.entity_id)

    def test_people_are_never_merged_by_embedding(self):
        """Two colleagues at one company are not one person."""
        person = entity(EntityKind.PERSON, "a@stridecore.com", "Anna")
        index = EntityIndex.build([person], vectors={person.entity_id: vec(1, 0, 0, 0)})
        result = resolve(
            [mention("person:b@stridecore.com", "Bram")],
            index,
            embeddings={"person:b@stridecore.com": vec(1, 0.01, 0, 0)},
        )
        self.assertEqual(result.merged, 0)

    def test_exact_key_wins_over_a_closer_vector(self):
        """Ladder order, asserted directly: rung 1 is a certainty and must not
        be second-guessed by a similarity score."""
        exact = entity(EntityKind.PROJECT, "atlas", "Atlas")
        other = entity(EntityKind.PROJECT, "henderson", "Henderson")
        index = EntityIndex.build(
            [exact, other], vectors={other.entity_id: vec(1, 0, 0, 0)}
        )
        result = resolve(
            [mention("project:atlas", "Atlas")],
            index,
            embeddings={"project:atlas": vec(1, 0, 0, 0)},
        )
        self.assertEqual(result.by_rung["exact"], 1)
        self.assertEqual(result.mentions[0].entity_id, exact.entity_id)

    def test_no_embeddings_means_fully_deterministic(self):
        result = resolve([mention("project:henderson issue", "Henderson issue")], self.index)
        self.assertEqual(result.by_rung["vector"], 0)
        self.assertEqual(result.created, 1)

    def test_mismatched_dimensions_are_skipped_not_fatal(self):
        result = resolve(
            [mention("project:x", "X")],
            self.index,
            embeddings={"project:x": vec(1, 0)},
        )
        self.assertEqual(result.created, 1)


class EntityFoldingTest(unittest.TestCase):
    def test_ids_are_stable_across_runs(self):
        """Content-derived ids make the whole pass idempotent, so persistence
        is an upsert and not a duplicate factory."""
        first = resolve([mention("project:atlas", "Atlas")])
        second = resolve([mention("project:atlas", "Atlas")])
        self.assertEqual(
            first.mentions[0].entity_id, second.mentions[0].entity_id
        )
        self.assertEqual(
            first.mentions[0].entity_id, entity_id_for(EntityKind.PROJECT, "atlas")
        )

    def test_timestamps_come_from_the_caller_not_a_clock(self):
        early = datetime(2026, 8, 1, tzinfo=timezone.utc)
        late = datetime(2026, 8, 20, tzinfo=timezone.utc)
        result = resolve(
            [mention("project:atlas", "Atlas", "e1"), mention("project:atlas", "Atlas", "e2")],
            received_at={"e1": late, "e2": early},
        )
        self.assertEqual(result.entities[0].first_seen, early)
        self.assertEqual(result.entities[0].last_seen, late)

    def test_no_timestamps_leaves_them_unset(self):
        result = resolve([mention("project:atlas", "Atlas")])
        self.assertIsNone(result.entities[0].first_seen)
        self.assertIsNone(result.entities[0].last_seen)

    def test_mention_counts_accumulate_across_the_batch(self):
        result = resolve([mention("project:atlas", "Atlas", "e{0}".format(i)) for i in range(5)])
        self.assertEqual(result.entities[0].mention_count, 5)

    def test_canonical_name_is_the_first_surface_form(self):
        result = resolve(
            [mention("project:atlas", "Atlas"), mention("project:atlas", "ATLAS!!", "e2")]
        )
        self.assertEqual(result.entities[0].canonical_name, "Atlas")


class DegenerateInputTest(unittest.TestCase):
    def test_already_resolved_mention_passes_through_untouched(self):
        opaque = mention("deadbeefdeadbeef", "Atlas")
        result = resolve([opaque])
        self.assertEqual(result.mentions, [opaque])
        self.assertEqual(result.entities, [])
        self.assertEqual(result.unresolved, 1)

    def test_empty_key_is_dropped(self):
        result = resolve([mention("project:", "")])
        self.assertEqual(result.mentions, [])
        self.assertEqual(result.unresolved, 1)

    def test_unknown_kind_prefix_is_dropped(self):
        result = resolve([mention("nonsense:x", "X")])
        self.assertEqual(result.unresolved, 1)

    def test_no_mentions(self):
        result = resolve([])
        self.assertEqual((result.mentions, result.entities, result.created), ([], [], 0))


if __name__ == "__main__":
    unittest.main()
