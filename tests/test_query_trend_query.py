"""TrendQuery model: contract fidelity (JSON Schema vs snapshot) and validation rules."""

import importlib
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, SKIP_REASON, SNAPSHOT_PATH, load_fixtures, load_snapshot


def normalize(node, defs):
    """Resolve $ref and drop annotation-only keys so Literal-based and Enum-based schemas compare equal."""
    if isinstance(node, dict):
        if "$ref" in node:
            return normalize(defs[node["$ref"].split("/")[-1]], defs)
        out = {}
        for key, value in node.items():
            if key in {"title", "description", "$defs"}:
                continue
            if key in {"enum", "required"}:
                out[key] = sorted(value)
            else:
                out[key] = normalize(value, defs)
        return out
    if isinstance(node, list):
        return [normalize(item, defs) for item in node]
    return node


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class TrendQueryModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("fashion_planner_orchestrator.query.trend_query")
        cls.pydantic = importlib.import_module("pydantic")

    @unittest.skipUnless(SNAPSHOT_PATH.exists(), "contract snapshot not present in this checkout")
    def test_json_schema_equals_the_contract_snapshot(self):
        snapshot_schema = load_snapshot()["json_schema"]
        local_schema = self.models.TrendQuery.model_json_schema()
        self.assertEqual(
            normalize(local_schema, local_schema["$defs"]),
            normalize(snapshot_schema, snapshot_schema["$defs"]),
        )

    def test_major_category_is_derived_from_minor(self):
        ref = self.models.CategoryRef(raw="코트", minor="코트")
        self.assertEqual(ref.major, "아우터")
        overridden = self.models.CategoryRef(raw="코트", major="상의", minor="코트")
        self.assertEqual(overridden.major, "아우터")
        only_major = self.models.CategoryRef(raw="상의", major="상의")
        self.assertIsNone(only_major.minor)

    def test_attribute_value_must_be_in_the_vocabulary_or_null(self):
        attribute = self.models.RequestedAttribute
        self.assertEqual(attribute(type="color", raw="핑크색", value="핑크").value, "핑크")
        self.assertIsNone(attribute(type="color", raw="카키베이지", value=None).value)
        with self.assertRaises(self.pydantic.ValidationError):
            attribute(type="color", raw="핑크색", value="핑크색")
        with self.assertRaises(self.pydantic.ValidationError):
            attribute(type="length", raw="롱", value=None)

    def test_gender_and_season_values_are_closed(self):
        with self.assertRaises(self.pydantic.ValidationError):
            self.models.GenderRef(raw="여성", value="여성")
        with self.assertRaises(self.pydantic.ValidationError):
            self.models.SeasonRequest(year=2027, season="SP", origin="user_requested")

    def test_default_next_season(self):
        season = self.models.SeasonRequest.default_next(date(2026, 11, 9))
        self.assertEqual(
            season.model_dump(), {"year": 2027, "season": "SS", "raw": None, "origin": "default_next"},
        )

    def test_every_fixture_expectation_is_a_valid_trend_query(self):
        for fixture in load_fixtures():
            with self.subTest(case=fixture["case"]):
                parsed = self.models.TrendQuery.model_validate(fixture["expected_trend_query"])
                self.assertEqual(parsed.model_dump(mode="json"), fixture["expected_trend_query"])


if __name__ == "__main__":
    unittest.main()
