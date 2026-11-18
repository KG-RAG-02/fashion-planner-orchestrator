"""Alias-dictionary port (query/lexicon.py) against the snapshot and the original trend code. Stdlib only.

- alias_data.py must be exactly what scripts/generate_alias_data.py renders from the snapshot.
- Every golden case (outputs of trend AliasMap, see contracts/agents/trend/vocabulary/alias-map.golden.json) must be
  reproduced exactly by the port.
Skipped when the snapshot files are not in this checkout.
"""

import importlib.util
import json
import unittest

from _query_support import ROOT
from fashion_planner_orchestrator.query import alias_data, lexicon
from fashion_planner_orchestrator.query.lexicon import Lexicon, Normalized, norm_key

ALIAS_SNAPSHOT_PATH = ROOT / "contracts" / "agents" / "trend" / "vocabulary" / "alias-map.snapshot.json"
ALIAS_GOLDEN_PATH = ROOT / "contracts" / "agents" / "trend" / "vocabulary" / "alias-map.golden.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_alias_data", ROOT / "scripts" / "generate_alias_data.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _row(normalized, fields):
    as_dict = normalized.as_dict()
    return [as_dict[name] for name in fields]


@unittest.skipUnless(ALIAS_SNAPSHOT_PATH.exists(), "alias-map snapshot not present in this checkout")
class AliasDataMatchesSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = _load(ALIAS_SNAPSHOT_PATH)
        cls.generator = _load_generator()

    def test_alias_data_module_is_the_generator_output(self):
        # Fails when the snapshot was replaced but `py scripts/generate_alias_data.py` was not run.
        expected = self.generator.render(self.snapshot)
        actual = self.generator.TARGET_PATH.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_snapshot_hash_matches_its_data(self):
        data = self.generator.snapshot_data(self.snapshot)
        self.assertEqual(self.generator.content_sha256(data), self.snapshot["_provenance"]["content_sha256"])

    def test_provenance_matches_the_snapshot(self):
        provenance = self.snapshot["_provenance"]
        self.assertEqual(alias_data.PROVENANCE["source_repo"], "fashion-planner-trend")
        self.assertEqual(alias_data.PROVENANCE["source_repo"], provenance["source_repo"])
        self.assertEqual(alias_data.PROVENANCE["content_sha256"], provenance["content_sha256"])
        # the snapshot names the repository and the content hash only (commit SHAs change when history is rewritten)
        self.assertFalse({"source_branch", "source_commit", "source_ref", "source_dirty"} & set(provenance))

    def test_snapshot_covers_the_eleven_attributes_and_text_guard(self):
        self.assertEqual(set(self.snapshot["attributes"]), set(alias_data.ATTRIBUTES))
        self.assertEqual(len(alias_data.ATTRIBUTES), 11)
        self.assertIn("guards", self.snapshot["text_guard"])
        for name in ("category_otpensource", "set_components", "set_overrides"):
            self.assertNotIn(name, self.snapshot["attributes"])

    def test_standard_values_agree_with_the_contract_vocabulary(self):
        from fashion_planner_orchestrator.query import vocabulary as vocab

        std = alias_data.STANDARD_VALUES
        self.assertEqual(std["category"], vocab.CATEGORY_MINOR)
        for attr in ("color", "material", "pattern", "fit"):
            self.assertEqual(set(std[attr]), set(vocab.ATTRIBUTE_VALUES[attr]), attr)
        detail_union = set(std["detail"]) | set(std["neckline"]) | set(std["collar"])
        self.assertEqual(detail_union, set(vocab.ATTRIBUTE_VALUES["detail"]))

    def test_melange_target_is_a_standard_color(self):
        self.assertIn(lexicon._MELANGE_VALUE, alias_data.STANDARD_VALUES["color"])


@unittest.skipUnless(ALIAS_GOLDEN_PATH.exists(), "alias-map golden data not present in this checkout")
class PortMatchesOriginalTests(unittest.TestCase):
    """Every expected output of the original trend code must be reproduced exactly."""

    @classmethod
    def setUpClass(cls):
        cls.golden = _load(ALIAS_GOLDEN_PATH)
        cls.fields = cls.golden["normalized_fields"]
        cls.lex = Lexicon()

    def _assert_cases(self, cases, actual_of, label):
        mismatches = []
        for raw, expected in cases:
            actual = actual_of(raw)
            if actual != expected:
                mismatches.append((raw, expected, actual))
        self.assertEqual(mismatches, [], f"{label}: {len(mismatches)} of {len(cases)} cases differ")

    def test_golden_was_made_from_this_snapshot(self):
        self.assertEqual(
            self.golden["_provenance"]["snapshot_content_sha256"], alias_data.PROVENANCE["content_sha256"],
        )

    def test_normalize(self):
        self.assertEqual(set(self.golden["normalize"]) - {"season"}, set(alias_data.ATTRIBUTES))
        for attr, cases in self.golden["normalize"].items():
            with self.subTest(attribute=attr):
                self.assertTrue(cases)
                self._assert_cases(
                    cases, lambda raw, a=attr: _row(self.lex.normalize(a, raw), self.fields), f"normalize[{attr}]",
                )

    def test_normalize_tokens(self):
        for attr, cases in self.golden["normalize_tokens"].items():
            with self.subTest(attribute=attr):
                self._assert_cases(
                    cases,
                    lambda raw, a=attr: [_row(n, self.fields) for n in self.lex.normalize_tokens(a, raw)],
                    f"normalize_tokens[{attr}]",
                )

    def test_normalize_color(self):
        self._assert_cases(
            self.golden["normalize_color"],
            lambda raw: [_row(n, self.fields) for n in self.lex.normalize_color(raw)],
            "normalize_color",
        )

    def test_split_tokens(self):
        self._assert_cases(self.golden["split_tokens"], self.lex.split_tokens, "split_tokens")

    def test_norm_key(self):
        self._assert_cases(self.golden["norm_key"], norm_key, "norm_key")


class LexiconSanityTests(unittest.TestCase):
    """A few hand-written cases that document the behaviour (and the decisions around it)."""

    def test_alias(self):
        n = lexicon.normalize("category", "슬랙스")
        self.assertEqual((n.value, n.matched_by, n.approx), ("팬츠", "alias", False))

    def test_standard_value_with_spelling_variants(self):
        for raw in ("오버사이즈", "오버 사이즈", "OVERSIZED", "over-sized"):
            with self.subTest(raw=raw):
                self.assertEqual(lexicon.normalize("fit", raw).value, "오버사이즈")

    def test_color_base_at_the_end_is_approx(self):
        [n] = lexicon.normalize_color("버터옐로우")
        self.assertEqual((n.value, n.approx, n.matched_by), ("옐로우", True, "approx"))

    def test_color_modifier_is_removed(self):
        [n] = lexicon.normalize_color("다크 네이비")
        self.assertEqual((n.value, n.approx, n.matched_by), ("네이비", False, "alias"))

    def test_multi_color_and_counts(self):
        values = [(n.value, n.matched_by) for n in lexicon.normalize_tokens("color", "블랙, 화이트/2color")]
        self.assertEqual(values, [("블랙", "standard"), ("화이트", "standard"), (None, "null_value")])
        [n] = lexicon.normalize_color("멜란지")
        self.assertEqual((n.value, n.matched_by), ("그레이", "alias"))

    def test_material_blend_retry_and_compound_value(self):
        [wool, poly] = lexicon.normalize_tokens("material", "울/폴리")
        self.assertEqual((wool.value, poly.value), ("울/캐시미어", "합성섬유"))
        [cotton] = lexicon.normalize_tokens("material", "면 혼방")
        self.assertEqual((cotton.raw, cotton.value, cotton.matched_by), ("면 혼방", "면", "standard"))
        self.assertEqual(lexicon.split_tokens("울/캐시미어, 시퀸/글리터"), ["울/캐시미어", "시퀸/글리터"])

    def test_approx_is_reported_not_hidden(self):
        n = lexicon.normalize("fit", "슬림핏")
        self.assertEqual((n.value, n.approx, n.matched_by), ("타이트", True, "approx"))

    def test_route(self):
        n = lexicon.normalize("fit", "부츠컷")
        self.assertEqual((n.route_to, n.value, n.matched_by), ("shape", "부츠컷", "route"))
        n = lexicon.normalize("detail", "프린트")
        self.assertEqual((n.route_to, n.value, n.matched_by), ("pattern", None, "route"))

    def test_hint_is_only_reported(self):
        # Decision H5: the caller never applies hints. The port reports them like the original.
        n = lexicon.normalize("material", "다운")
        self.assertEqual((n.value, n.matched_by, n.hint_dict()), (None, "hint", {"category_minor": "패딩"}))

    def test_null_value_and_unknown(self):
        self.assertEqual(lexicon.normalize("category", "모자").matched_by, "null_value")
        self.assertEqual(lexicon.normalize("fit", "오버핏").matched_by, "none")  # dictionary gap (L-11)

    def test_season_word_is_not_a_dictionary_key(self):
        lex = lexicon.default_lexicon()
        for attr in alias_data.ATTRIBUTES:
            with self.subTest(attribute=attr):
                self.assertNotIn(norm_key("겨울"), lex.index(attr))
                self.assertEqual(lexicon.normalize(attr, "겨울").matched_by, "none")
        # ...but `울` is a one-character material alias (the scanner must flag 겨울 -> 울, plan S3)
        self.assertEqual(lexicon.normalize("material", "울").value, "울/캐시미어")

    def test_empty_and_unknown_attribute(self):
        self.assertEqual(lexicon.normalize("color", None), Normalized(raw=""))
        self.assertEqual(lexicon.normalize("color", "  "), Normalized(raw=""))
        self.assertEqual(lexicon.normalize("season", "SS").matched_by, "none")
        self.assertEqual(lexicon.split_tokens(None), [])

    def test_result_is_frozen_and_hashable(self):
        n = lexicon.normalize("material", "다운")
        with self.assertRaises(Exception):
            n.value = "x"  # type: ignore[misc]
        self.assertEqual(hash(n), hash(lexicon.normalize("material", "다운")))


if __name__ == "__main__":
    unittest.main()
