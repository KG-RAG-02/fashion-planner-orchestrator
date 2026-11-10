"""StructuredQuery 1.0 model: contract examples, validation rules, JSON Schema export, and the
intended differences from trend's TrendQuery snapshot (docs/contracts/structured-query.md)."""

import copy
import importlib
import importlib.util
import json
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, ROOT, SKIP_REASON, SNAPSHOT_PATH, load_snapshot
from vogue_orchestrator.query import vocabulary as vocab

SCHEMA_PATH = ROOT / "docs" / "contracts" / "structured-query.schema.json"
HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None

EMPTY_ATTRIBUTES = {"color": [], "material": [], "pattern": [], "detail": [], "fit": []}
DEFAULT_SEASON = {"year": 2027, "season": "SS", "raw": None, "origin": "default_next"}


def full_query(request_id, original_query, *, requested=None, unsupported=None, trend_terms=None):
    """A complete StructuredQuery document; the contract examples only show the parts that matter."""
    return {
        "schema_version": "1.0",
        "request_id": request_id,
        "original_query": original_query,
        "trend_terms": trend_terms or {"season": dict(DEFAULT_SEASON), "style_names": [], "item_terms": []},
        "requested": requested or {
            "categories": {"include": [], "exclude": []},
            "gender": None,
            "attributes": {"include": dict(EMPTY_ATTRIBUTES), "exclude": dict(EMPTY_ATTRIBUTES)},
        },
        "unsupported": unsupported or [],
        "missing_fields": [],
    }


# Contract section 2 (overall shape), with the gender and brand samples from its comments.
SECTION_2_SHAPE = full_query(
    "req-001", "사용자 원문",
    trend_terms={"season": dict(DEFAULT_SEASON), "style_names": [{"raw": "발레코어", "value": None}], "item_terms": []},
    requested={
        "categories": {"include": [], "exclude": []},
        "gender": {"raw": "여성", "value": "여", "match": "alias"},
        "attributes": {"include": dict(EMPTY_ATTRIBUTES), "exclude": dict(EMPTY_ATTRIBUTES)},
    },
    unsupported=[{"type": "brand", "raw": "자라", "reason": "Product 데이터는 Musinsa뿐", "polarity": "exclude"}],
)

# Contract section 6, example 1: "블랙 말고 니트웨어 보여줘" (the `requested` block is copied verbatim).
EXAMPLE_1 = full_query("req-ex1", "블랙 말고 니트웨어 보여줘", requested={
    "categories": {"include": [{"raw": "니트웨어", "major": "상의", "minor": "니트웨어", "match": "exact"}], "exclude": []},
    "gender": None,
    "attributes": {
        "include": {"color": [], "material": [], "pattern": [], "detail": [], "fit": []},
        "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}],
                    "material": [], "pattern": [], "detail": [], "fit": []},
    },
})

# Example 2: "슬랙스 빼고 하의로 기획해줘" (the `categories` block is copied verbatim).
EXAMPLE_2 = full_query("req-ex2", "슬랙스 빼고 하의로 기획해줘", requested={
    "categories": {
        "include": [{"raw": "하의", "major": "하의", "minor": None, "match": "exact"}],
        "exclude": [{"raw": "슬랙스", "major": "하의", "minor": "팬츠", "match": "alias"}],
    },
    "gender": None,
    "attributes": {"include": dict(EMPTY_ATTRIBUTES), "exclude": dict(EMPTY_ATTRIBUTES)},
})

# Example 3: mixed provenance (the whole document is copied verbatim).
EXAMPLE_3 = {
    "schema_version": "1.0",
    "request_id": "req-003",
    "original_query": "꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고",
    "trend_terms": {"season": {"year": 2027, "season": "SS", "raw": None, "origin": "default_next"},
                    "style_names": [], "item_terms": []},
    "requested": {
        "categories": {"include": [{"raw": "후드티", "major": "상의", "minor": "후드티", "match": "exact"}], "exclude": []},
        "gender": None,
        "attributes": {
            "include": {"color": [{"raw": "버터옐로우", "value": "옐로우", "match": "approx"}],
                        "material": [], "pattern": [], "detail": [],
                        "fit": [{"raw": "오버핏", "value": "오버사이즈", "match": "llm"}]},
            "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}],
                        "material": [], "pattern": [], "detail": [], "fit": []},
        },
    },
    "unsupported": [{"type": "abstract", "raw": "꾸안꾸 느낌", "reason": "표준 어휘로 바꿀 수 없는 추상 표현", "polarity": "include"}],
    "missing_fields": [],
}

# Example 4: "가방이랑 신발은 빼고, 자라 말고" (the `unsupported` list is copied verbatim).
EXAMPLE_4 = full_query("req-ex4", "가방이랑 신발은 빼고, 자라 말고", unsupported=[
    {"type": "category", "raw": "가방", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "polarity": "exclude"},
    {"type": "category", "raw": "신발", "reason": "표준 카테고리(상분류 4·하분류 21)에 없음", "polarity": "exclude"},
    {"type": "brand", "raw": "자라", "reason": "Product 데이터는 Musinsa뿐", "polarity": "exclude"},
])

CONTRACT_EXAMPLES = {
    "section 2 shape": SECTION_2_SHAPE,
    "example 1": EXAMPLE_1,
    "example 2": EXAMPLE_2,
    "example 3": EXAMPLE_3,
    "example 4": EXAMPLE_4,
}

# Keys that express producer-side policy rather than shape; checked separately in
# test_every_object_is_closed_and_fully_required, so the TrendQuery comparison shows only shape changes.
POLICY_KEYS = {"additionalProperties", "required", "default", "minLength", "$schema"}

# Every shape difference between trend's TrendQuery snapshot and StructuredQuery 1.0.
# (kind, path) -> reason. Contract section 7 rows are named; "tightening" rows are constraints this
# repository adds as the producer (no new information, fewer allowed values). Any other difference fails.
_CATEGORY_ITEMS = "properties.requested.properties.categories.properties.{side}.items.properties"
_TERM_VALUE = "properties.trend_terms.properties.{field}.items.properties.value"
INTENDED_DIFFERENCES = {
    ("added", "properties.schema_version"): "section 7: version",
    ("removed", "properties.requested.properties.attributes.items"): "section 7: attributes include/exclude per kind (X2)",
    ("added", "properties.requested.properties.attributes.properties"): "section 7: attributes include/exclude per kind (X2)",
    ("changed", "properties.requested.properties.attributes.type"): "section 7: attributes include/exclude per kind (X2)",
    **{
        key: reason
        for side in ("include", "exclude")
        for key, reason in {
            ("added", _CATEGORY_ITEMS.format(side=side) + ".match"): "section 7: match on every value",
            ("removed", _CATEGORY_ITEMS.format(side=side) + ".major.anyOf"):
                "tightening: a CategoryRef is always resolved, so major is never null",
            ("added", _CATEGORY_ITEMS.format(side=side) + ".major.enum"):
                "tightening: a CategoryRef is always resolved, so major is never null",
            ("added", _CATEGORY_ITEMS.format(side=side) + ".major.type"):
                "tightening: a CategoryRef is always resolved, so major is never null",
        }.items()
    },
    ("added", "properties.requested.properties.gender.anyOf[0].properties.match"): "section 7: match on every value",
    ("removed", "properties.requested.properties.gender.anyOf[0].properties.raw.anyOf"):
        "tightening: a GenderRef always keeps the user's raw text",
    ("added", "properties.requested.properties.gender.anyOf[0].properties.raw.type"):
        "tightening: a GenderRef always keeps the user's raw text",
    **{
        key: "tightening: no vocabulary for trend names / product terms yet, so value is always null"
        for field in ("style_names", "item_terms")
        for key in (("removed", _TERM_VALUE.format(field=field) + ".anyOf"),
                    ("added", _TERM_VALUE.format(field=field) + ".type"))
    },
    ("added", "properties.unsupported.items.properties.polarity"): "section 7: polarity on unsupported conditions",
}


def normalize(node, defs, drop=frozenset()):
    """Resolve $ref and drop annotation keys (and `drop`) so two schemas compare by shape."""
    if isinstance(node, dict):
        if "$ref" in node:
            return normalize(defs[node["$ref"].split("/")[-1]], defs, drop)
        out = {}
        for key, value in node.items():
            if key in {"title", "description", "$defs"} | drop:
                continue
            out[key] = sorted(value) if key in {"enum", "required"} else normalize(value, defs, drop)
        return out
    if isinstance(node, list):
        return [normalize(item, defs, drop) for item in node]
    return node


def schema_diff(old, new, path=""):
    """Yield (kind, path) for every place where `new` differs from `old`."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key
            if key not in old:
                yield "added", child
            elif key not in new:
                yield "removed", child
            else:
                yield from schema_diff(old[key], new[key], child)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new) and all(
        isinstance(item, dict) for item in old + new
    ):
        for index, (old_item, new_item) in enumerate(zip(old, new)):
            yield from schema_diff(old_item, new_item, f"{path}[{index}]")
    elif old != new:
        yield "changed", path


def _load_exporter():
    spec = importlib.util.spec_from_file_location(
        "export_structured_query_schema", ROOT / "scripts" / "export_structured_query_schema.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuredQueryExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("vogue_orchestrator.query.models")

    def test_contract_examples_validate_and_round_trip(self):
        for name, document in CONTRACT_EXAMPLES.items():
            with self.subTest(example=name):
                parsed = self.models.StructuredQuery.model_validate(document)
                self.assertEqual(parsed.model_dump(mode="json"), document)

    def test_minimal_document_is_emitted_with_every_field(self):
        query = self.models.StructuredQuery(
            request_id="r", original_query="q",
            trend_terms={"season": self.models.SeasonRequest.default_next(date(2026, 11, 10))},
        )
        self.assertEqual(query.model_dump(mode="json"), full_query("r", "q"))
        self.assertEqual(query.schema_version, self.models.SCHEMA_VERSION)

    def test_package_exports_the_contract(self):
        package = importlib.import_module("vogue_orchestrator.query")
        self.assertIs(package.StructuredQuery, self.models.StructuredQuery)
        self.assertEqual(package.SCHEMA_VERSION, "1.0")
        self.assertIs(package.TrendQuery, importlib.import_module("vogue_orchestrator.query.trend_query").TrendQuery)


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuredQueryValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("vogue_orchestrator.query.models")
        cls.ValidationError = importlib.import_module("pydantic").ValidationError

    def assert_invalid(self, document):
        with self.assertRaises(self.ValidationError):
            self.models.StructuredQuery.model_validate(document)

    def with_attribute(self, side, kind, ref):
        document = copy.deepcopy(EXAMPLE_3)
        document["requested"]["attributes"][side][kind] = [ref]
        return document

    # --- values and the closed vocabulary -------------------------------------------------------

    def test_attribute_kinds_and_values_follow_the_vocabulary(self):
        self.assertEqual(tuple(self.models.AttributeValues.model_fields), vocab.ATTRIBUTE_TYPES)
        self.assertEqual(tuple(self.models.ATTRIBUTE_REF_TYPES), vocab.ATTRIBUTE_TYPES)
        self.assertEqual(len(vocab.ATTRIBUTE_VALUES["detail"]), 61)
        for kind, ref_type in self.models.ATTRIBUTE_REF_TYPES.items():
            with self.subTest(kind=kind):
                enum = ref_type.model_json_schema()["properties"]["value"]["anyOf"][0]["enum"]
                self.assertEqual(tuple(enum), vocab.ATTRIBUTE_VALUES[kind])
                self.assertEqual(tuple(ref_type.model_fields), ("raw", "value", "match"))

    def test_value_outside_the_kind_vocabulary_is_rejected(self):
        cases = [
            ("color", "핑크색"),        # not a standard value
            ("color", "브이넥"),        # a standard value of another kind
            ("detail", "블랙"),
            ("fit", "슬림"),            # an alias, not the standard value
        ]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                self.assert_invalid(self.with_attribute("include", kind, {"raw": value, "value": value, "match": "exact"}))

    def test_detail_holds_neckline_and_collar_values(self):
        for value in ("셔링", "터틀넥", "셔츠칼라", "후드"):
            with self.subTest(value=value):
                document = self.with_attribute("exclude", "detail", {"raw": value, "value": value, "match": "exact"})
                self.models.StructuredQuery.model_validate(document)

    def test_match_is_none_exactly_when_value_is_null(self):
        valid = {"raw": "연두", "value": None, "match": "none"}
        self.models.StructuredQuery.model_validate(self.with_attribute("include", "color", valid))
        for ref in ({"raw": "연두", "value": "그린", "match": "none"}, {"raw": "연두", "value": None, "match": "llm"},
                    {"raw": "연두", "value": "그린", "match": "guess"}):
            with self.subTest(ref=ref):
                self.assert_invalid(self.with_attribute("include", "color", ref))

    def test_gender_value_and_match(self):
        GenderRef = self.models.GenderRef
        self.assertEqual(GenderRef(raw="여성", value="여", match="alias").value, "여")
        self.assertIsNone(GenderRef(raw="젠더리스 느낌", value=None, match="none").value)
        for kwargs in ({"raw": "여성", "value": "여성", "match": "alias"}, {"raw": "여성", "value": "여", "match": "none"},
                       {"raw": "", "value": "여", "match": "exact"}, {"value": "여", "match": "exact"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(self.ValidationError):
                    GenderRef(**kwargs)

    # --- attribute keys ---------------------------------------------------------------------------

    def test_missing_attribute_keys_are_filled_with_empty_lists(self):
        document = copy.deepcopy(EXAMPLE_1)
        document["requested"]["attributes"] = {"exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}]}}
        dumped = self.models.StructuredQuery.model_validate(document).model_dump(mode="json")
        self.assertEqual(dumped, EXAMPLE_1)

    def test_null_or_unknown_attribute_keys_are_rejected(self):
        null_key = copy.deepcopy(EXAMPLE_1)
        null_key["requested"]["attributes"]["include"]["color"] = None
        self.assert_invalid(null_key)
        unknown_key = copy.deepcopy(EXAMPLE_1)
        unknown_key["requested"]["attributes"]["include"]["length"] = []
        self.assert_invalid(unknown_key)
        old_shape = copy.deepcopy(EXAMPLE_1)
        old_shape["requested"]["attributes"] = [{"type": "color", "raw": "블랙", "value": "블랙"}]
        self.assert_invalid(old_shape)

    def test_empty_lists_are_never_null(self):
        for path in (("unsupported",), ("missing_fields",), ("trend_terms", "style_names"),
                     ("requested", "categories", "include")):
            with self.subTest(path=path):
                document = copy.deepcopy(EXAMPLE_1)
                node = document
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = None
                self.assert_invalid(document)

    # --- categories -------------------------------------------------------------------------------

    def test_major_is_derived_from_minor_and_a_contradiction_is_rejected(self):
        CategoryRef = self.models.CategoryRef
        self.assertEqual(CategoryRef(raw="블레이저", minor="재킷", match="llm").major, "아우터")
        self.assertEqual(CategoryRef(raw="코트", major="아우터", minor="코트", match="exact").major, "아우터")
        with self.assertRaises(self.ValidationError):
            CategoryRef(raw="코트", major="상의", minor="코트", match="exact")

    def test_category_ref_is_always_resolved(self):
        CategoryRef = self.models.CategoryRef
        for kwargs in (
            {"raw": "가방", "major": None, "minor": None, "match": "none"},  # goes to `unsupported` instead
            {"raw": "가방", "minor": None, "match": "none"},
            {"raw": "후드티", "minor": "후드티", "match": "none"},
            {"raw": "가방", "major": "가방", "match": "exact"},
            {"raw": "바텀", "major": "하의", "minor": None, "match": "llm"},  # major-only must be named by the user
            {"raw": "윗옷", "major": "상의", "match": "alias"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(self.ValidationError):
                    CategoryRef(**kwargs)

    # --- envelope ---------------------------------------------------------------------------------

    def test_unknown_fields_are_rejected_at_every_level(self):
        def top(d):
            d["brand"] = "자라"

        def category(d):
            d["requested"]["categories"]["include"][0]["confidence"] = 0.9

        def attribute(d):
            d["requested"]["attributes"]["exclude"]["color"][0]["type"] = "color"

        def unsupported(d):
            d["unsupported"][0]["note"] = "x"

        def season(d):
            d["trend_terms"]["season"]["month"] = 3

        for mutate in (top, category, attribute, unsupported, season):
            with self.subTest(mutation=mutate.__name__):
                document = copy.deepcopy(EXAMPLE_3)
                mutate(document)
                self.assert_invalid(document)

    def test_schema_version_must_be_1_0(self):
        for version in ("2.0", "1", 1.0, None):
            with self.subTest(version=version):
                document = copy.deepcopy(EXAMPLE_3)
                document["schema_version"] = version
                self.assert_invalid(document)

    def test_unsupported_condition_needs_a_polarity(self):
        for polarity in (None, "maybe", "INCLUDE"):
            with self.subTest(polarity=polarity):
                document = copy.deepcopy(EXAMPLE_4)
                document["unsupported"][0]["polarity"] = polarity
                self.assert_invalid(document)
        document = copy.deepcopy(EXAMPLE_4)
        del document["unsupported"][0]["polarity"]
        self.assert_invalid(document)

    def test_season_raw_follows_its_origin(self):
        SeasonRequest = self.models.SeasonRequest
        self.assertEqual(SeasonRequest(year=2027, season="FW", raw="27 가을", origin="user_requested").raw, "27 가을")
        for kwargs in ({"year": 2027, "season": "SS", "raw": "봄", "origin": "default_next"},
                       {"year": 2027, "season": "SS", "raw": None, "origin": "user_requested"},
                       {"year": 2027, "season": "SP", "raw": None, "origin": "default_next"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(self.ValidationError):
                    SeasonRequest(**kwargs)

    def test_trend_term_value_is_always_null(self):
        self.assertIsNone(self.models.TermRef(raw="발레코어").value)
        with self.assertRaises(self.ValidationError):
            self.models.TermRef(raw="발레코어", value="발레코어")

    def test_ids_and_raw_texts_are_not_empty(self):
        for key in ("request_id", "original_query"):
            with self.subTest(key=key):
                document = copy.deepcopy(EXAMPLE_3)
                document[key] = ""
                self.assert_invalid(document)
        self.assert_invalid(self.with_attribute("include", "fit", {"raw": "", "value": "루즈", "match": "exact"}))


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuredQuerySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("vogue_orchestrator.query.models")
        cls.exporter = _load_exporter()
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_committed_schema_is_the_exporter_output(self):
        # Fails when the models changed but `py scripts/export_structured_query_schema.py` was not run.
        self.assertEqual(SCHEMA_PATH.read_text(encoding="utf-8"), self.exporter.render())
        self.assertEqual(self.exporter.TARGET_PATH, SCHEMA_PATH)

    def test_export_is_deterministic_and_unescaped(self):
        text = self.exporter.render()
        self.assertEqual(text, self.exporter.render())
        self.assertIn("블랙", text)
        self.assertTrue(text.endswith("}\n"))

    def test_every_object_is_closed_and_fully_required(self):
        objects = [self.schema] + list(self.schema["$defs"].values())
        for node in objects:
            if node.get("type") != "object":
                continue
            with self.subTest(model=node["title"]):
                self.assertIs(node["additionalProperties"], False)
                self.assertEqual(sorted(node["required"]), sorted(node["properties"]))

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema is not installed")
    def test_contract_examples_validate_against_the_exported_schema(self):
        jsonschema = importlib.import_module("jsonschema")
        validator = jsonschema.Draft202012Validator(self.schema)
        for name, document in CONTRACT_EXAMPLES.items():
            with self.subTest(example=name):
                self.assertEqual([error.message for error in validator.iter_errors(document)], [])
        bad = copy.deepcopy(EXAMPLE_3)
        bad["requested"]["attributes"]["include"]["color"][0]["value"] = "버터옐로우"
        del bad["requested"]["attributes"]["exclude"]["fit"]
        self.assertEqual(len(list(validator.iter_errors(bad))), 2)

    @unittest.skipUnless(SNAPSHOT_PATH.exists(), "contract snapshot not present in this checkout")
    def test_differences_from_the_trend_query_snapshot_are_the_intended_ones(self):
        snapshot = load_snapshot()["json_schema"]
        old = normalize(snapshot, snapshot["$defs"], POLICY_KEYS)
        new = normalize(self.schema, self.schema["$defs"], POLICY_KEYS)
        self.assertEqual(sorted(schema_diff(old, new)), sorted(INTENDED_DIFFERENCES))


if __name__ == "__main__":
    unittest.main()
