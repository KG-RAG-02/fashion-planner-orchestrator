"""Vocabulary drift check against the contract snapshot, and season arithmetic. Stdlib only."""

import importlib.util
import unittest
from datetime import date

from _query_support import ROOT, SNAPSHOT_PATH, load_snapshot
from fashion_planner_orchestrator.query import vocabulary as vocab
from fashion_planner_orchestrator.query.season import next_fashion_season, resolve_season_year


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_query_vocabulary", ROOT / "scripts" / "generate_query_vocabulary.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(SNAPSHOT_PATH.exists(), "contract snapshot not present in this checkout")
class VocabularyMatchesSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_snapshot()

    def test_value_sets_equal_the_snapshot(self):
        v = self.snapshot["vocabulary"]
        self.assertEqual(vocab.SEASONS, tuple(v["season"]))
        self.assertEqual(vocab.GENDERS, tuple(v["gender"]))
        self.assertEqual(vocab.CATEGORY_MAJOR, tuple(v["category_major"]))
        self.assertEqual(vocab.CATEGORY_MINOR, tuple(v["category_minor"]))
        self.assertEqual(vocab.CATEGORY_MINOR_TO_MAJOR, self.snapshot["category_minor_to_major"])
        self.assertEqual(
            {k: tuple(values) for k, values in v["attribute_values"].items()}, vocab.ATTRIBUTE_VALUES,
        )
        self.assertEqual(set(vocab.ATTRIBUTE_TYPES), set(v["attribute_values"]))

    def test_provenance_matches_the_snapshot(self):
        provenance = self.snapshot["_provenance"]
        self.assertEqual(vocab.PROVENANCE["source_commit"], provenance["source_commit"])
        self.assertEqual(vocab.PROVENANCE["content_sha256"], provenance["content_sha256"])

    def test_vocabulary_module_is_the_generator_output(self):
        # Fails when the snapshot was replaced but `py scripts/generate_query_vocabulary.py` was not run.
        generator = _load_generator()
        expected = generator.render(self.snapshot)
        actual = generator.TARGET_PATH.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_every_minor_category_maps_to_a_major_one(self):
        self.assertEqual(set(vocab.CATEGORY_MINOR_TO_MAJOR), set(vocab.CATEGORY_MINOR))
        self.assertTrue(set(vocab.CATEGORY_MINOR_TO_MAJOR.values()) <= set(vocab.CATEGORY_MAJOR))


class SeasonTests(unittest.TestCase):
    def test_next_fashion_season_table(self):
        cases = {
            date(2026, 3, 1): (2026, "FW"),
            date(2026, 5, 1): (2026, "FW"),
            date(2026, 8, 31): (2026, "FW"),
            date(2026, 9, 1): (2027, "SS"),
            date(2026, 11, 9): (2027, "SS"),
            date(2026, 12, 31): (2027, "SS"),
            date(2027, 1, 15): (2027, "SS"),
            date(2027, 2, 28): (2027, "SS"),
        }
        for today, expected in cases.items():
            with self.subTest(today=today):
                self.assertEqual(next_fashion_season(today), expected)

    def test_year_of_a_season_named_without_year_assumption_a1(self):
        cases = [
            ("SS", date(2026, 11, 9), 2027),
            ("FW", date(2026, 11, 9), 2027),
            ("FW", date(2026, 5, 1), 2026),
            ("SS", date(2026, 5, 1), 2027),
            ("SS", date(2027, 1, 10), 2027),
            ("FW", date(2027, 1, 10), 2027),
        ]
        for season, today, expected in cases:
            with self.subTest(season=season, today=today):
                self.assertEqual(resolve_season_year(season, today), expected)

    def test_unknown_season_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_season_year("SP", date(2026, 11, 9))


if __name__ == "__main__":
    unittest.main()
