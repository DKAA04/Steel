"""Near-duplicate and related-fact matching (English and Spanish, invented data)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openjarvis.memory.store import TRUST_UNTRUSTED

from steel.facts import SteelFactStore
from steel.similarity import is_near_duplicate, is_related


class SimilarityTest(unittest.TestCase):
    def test_case_punctuation_and_filler_are_ignored(self) -> None:
        self.assertTrue(
            is_near_duplicate(
                "User's favorite tea is genmaicha.", "user's FAVORITE tea is genmaicha"
            )
        )
        self.assertTrue(
            is_near_duplicate("User has a peanut allergy.", "User has peanut allergy")
        )
        self.assertTrue(is_near_duplicate("User owns 2 cats", "The user owns 2 cats."))

    def test_spanish_accents_and_filler(self) -> None:
        self.assertTrue(
            is_near_duplicate("El usuario vive en Málaga.", "Vive en Malaga")
        )
        self.assertTrue(
            is_near_duplicate(
                "Lucía es profesora de química.", "lucia es profesora de quimica"
            )
        )
        self.assertTrue(
            is_near_duplicate("Los gatos del usuario", "El usuario tiene un gato")
        )

    def test_negation_and_numbers_must_match(self) -> None:
        self.assertFalse(is_near_duplicate("User likes tea", "User does not like tea"))
        self.assertFalse(
            is_near_duplicate("El usuario come carne", "El usuario no come carne")
        )
        self.assertFalse(is_near_duplicate("User has 2 cats", "User has 3 cats"))

    def test_different_facts_are_kept(self) -> None:
        self.assertFalse(
            is_near_duplicate("User has a peanut allergy", "User has a sesame allergy")
        )
        self.assertFalse(
            is_near_duplicate("User works as a nurse in Leeds", "User lives in Leeds")
        )

    def test_only_a_new_fact_that_adds_nothing_is_skipped(self) -> None:
        self.assertTrue(is_near_duplicate("User likes tea", "User likes tea with milk"))
        # The richer fact adds "milk", so it is kept even though "likes tea" exists.
        self.assertFalse(
            is_near_duplicate("User likes tea with milk", "User likes tea")
        )
        # Saying who it is adds the name; saying "the user" after the name does not.
        self.assertFalse(
            is_near_duplicate("Lucía vive en Valencia", "El usuario vive en Valencia")
        )
        self.assertTrue(
            is_near_duplicate("El usuario vive en Valencia", "Lucía vive en Valencia")
        )

    def test_known_misses_are_paraphrases(self) -> None:
        # Documented limit: no synonyms, so these are NOT detected.
        self.assertFalse(
            is_near_duplicate(
                "User works as a nurse in Leeds", "User's profession is nursing"
            )
        )

    def test_related_is_looser_than_duplicate(self) -> None:
        a, b = (
            "User's favorite tea is genmaicha",
            "User enjoys genmaicha with a roasted rice flavor",
        )
        self.assertFalse(is_near_duplicate(a, b))
        self.assertTrue(is_related(a, b))
        self.assertFalse(is_related("User likes cello", "User lives in Leeds"))


class StoreGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = SteelFactStore(Path(self._tmp.name) / "memory_facts.jsonl")

    def test_near_duplicate_is_skipped_not_stored(self) -> None:
        self.assertTrue(self.store.add("User has a peanut allergy."))
        result = self.store.add_checked("User has peanut allergy")
        self.assertEqual(result.outcome, "duplicate")
        self.assertTrue(result.existing_id.startswith("f-"))
        self.assertEqual(self.store.count(), 1)

    def test_distinct_and_richer_facts_are_stored(self) -> None:
        self.assertTrue(self.store.add("User likes tea"))
        self.assertTrue(self.store.add("User likes tea with milk"))
        self.assertEqual(self.store.count(), 2)

    def test_quarantined_copy_still_quarantines_the_clean_one(self) -> None:
        # Upstream rule kept intact: a later hostile copy must be able to
        # quarantine an older clean copy, so the guard must not intercept it.
        self.store.add("User has a peanut allergy", trust="auto")
        self.store.add("User has a peanut allergy", trust=TRUST_UNTRUSTED)
        self.assertFalse(self.store.list()[0].trusted_for_recall)


if __name__ == "__main__":
    unittest.main()
