"""StructuredQuery 1.0 model: contract examples, validation rules, JSON Schema export, and the
intended differences from trend's TrendQuery snapshot (contracts/agents/orchestrator/output/structured-query.md).

The conditions are grouped per item: `items[]` with category,
excluded categories, gender, attributes and unsupported conditions, plus `out_of_scope_items`."""

import copy
import importlib
import importlib.util
import json
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, ROOT, SKIP_REASON, SNAPSHOT_PATH, load_snapshot
from fashion_planner_orchestrator.query import contract_vocabulary as cvocab
from fashion_planner_orchestrator.query import vocabulary as vocab

SCHEMA_PATH = ROOT / "contracts" / "agents" / "orchestrator" / "output" / "structured-query.schema.json"
HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None

EMPTY_ATTRIBUTES = {"color": [], "material": [], "pattern": [], "detail": [], "fit": [],
                    "body_length": [], "sleeve_length": []}
NO_GENDER = {"include": [], "exclude": []}
DEFAULT_SEASON = {"year": 2027, "season": "SS", "raw": None, "origin": "default_next"}
CATEGORY_REASON = "표준 카테고리(상분류 4·하분류 21)에 없음"
BRAND_REASON = "Product 데이터는 Musinsa뿐"


def query_item(category=None, *, item_id="i1", excluded=(), gender=None, include=None, exclude=None, unsupported=()):
    """A complete item; the contract examples leave out empty lists for space."""
    return {
        "item_id": item_id,
        "category": category,
        "excluded_categories": list(excluded),
        "gender": gender or {"include": [], "exclude": []},
        "attributes": {"include": {**EMPTY_ATTRIBUTES, **(include or {})}, "exclude": {**EMPTY_ATTRIBUTES, **(exclude or {})}},
        "unsupported": list(unsupported),
    }


def full_query(request_id, original_query, *, items=(), out_of_scope=(), trend_terms=None):
    """A complete StructuredQuery document."""
    return {
        "schema_version": "1.0.0",
        "request_id": request_id,
        "original_query": original_query,
        "trend_terms": trend_terms or {"seasons": [dict(DEFAULT_SEASON)], "style_names": [], "product_model_names": []},
        "items": list(items),
        "out_of_scope_items": list(out_of_scope),
        "missing_fields": [],
    }


def cat(raw, major, minor, match):
    return {"raw": raw, "major": major, "minor": minor, "match": match}


def ref(raw, value, match):
    return {"raw": raw, "value": value, "match": match}


# Contract section 2 (overall shape), with the samples from its comments.
SECTION_2_SHAPE = full_query(
    "req-001", "사용자 원문",
    trend_terms={"seasons": [dict(DEFAULT_SEASON)], "style_names": [{"raw": "발레코어", "value": None}],
                 "product_model_names": []},
    items=[query_item(cat("블라우스", "상의", "블라우스", "exact"),
                      gender={"include": [ref("여성", "여", "alias")], "exclude": []},
                      unsupported=[{"type": "brand", "raw": "자라", "reason": BRAND_REASON, "polarity": "exclude"}])],
    out_of_scope=[{"raw": "부츠", "reason": CATEGORY_REASON, "conditions_raw": ["블랙"]}],
)

# Contract section 6, example 1: "블랙 말고 니트웨어 보여줘"
EXAMPLE_1 = full_query("req-ex1", "블랙 말고 니트웨어 보여줘", items=[
    query_item(cat("니트웨어", "상의", "니트웨어", "exact"), exclude={"color": [ref("블랙", "블랙", "exact")]}),
])

# Example 2: "슬랙스 빼고 하의로 기획해줘" - the user named the major category; the excluded minor stays in it
EXAMPLE_2 = full_query("req-ex2", "슬랙스 빼고 하의로 기획해줘", items=[
    query_item(cat("하의", "하의", None, "exact"), excluded=[cat("슬랙스", "하의", "팬츠", "alias")]),
])

# Example 3: mixed provenance (the whole document is copied verbatim from the contract).
EXAMPLE_3 = {
    "schema_version": "1.0.0",
    "request_id": "req-003",
    "original_query": "꾸안꾸 느낌 버터옐로우 오버핏 후드티 기획해줘, 블랙은 빼고",
    "trend_terms": {"seasons": [{"year": 2027, "season": "SS", "raw": None, "origin": "default_next"}],
                    "style_names": [], "product_model_names": []},
    "items": [
        {
            "item_id": "i1",
            "category": {"raw": "후드티", "major": "상의", "minor": "후드티", "match": "exact"},
            "excluded_categories": [],
            "gender": {"include": [], "exclude": []},
            "attributes": {
                "include": {"color": [{"raw": "버터옐로우", "value": "옐로우", "match": "approx"}],
                            "material": [], "pattern": [], "detail": [],
                            "fit": [{"raw": "오버핏", "value": "오버사이즈", "match": "llm"}],
                            "body_length": [], "sleeve_length": []},
                "exclude": {"color": [{"raw": "블랙", "value": "블랙", "match": "exact"}],
                            "material": [], "pattern": [], "detail": [], "fit": [], "body_length": [], "sleeve_length": []},
            },
            "unsupported": [{"type": "abstract", "raw": "꾸안꾸 느낌", "reason": "표준 어휘로 바꿀 수 없는 추상 표현",
                             "polarity": "include"}],
        }
    ],
    "out_of_scope_items": [],
    "missing_fields": [],
}

# Example 4: "가방이랑 신발은 빼고, 자라 말고" - no item named: one item over every category
EXAMPLE_4 = full_query("req-ex4", "가방이랑 신발은 빼고, 자라 말고", items=[query_item(unsupported=[
    {"type": "category", "raw": "가방", "reason": CATEGORY_REASON, "polarity": "exclude"},
    {"type": "category", "raw": "신발", "reason": CATEGORY_REASON, "polarity": "exclude"},
    {"type": "brand", "raw": "자라", "reason": BRAND_REASON, "polarity": "exclude"},
])])

# Example 5: "남성 제외하고 숏패딩이랑 반팔 니트 원피스 보여줘" - two items; the common gender exclusion is copied
_NOT_MEN = {"include": [], "exclude": [ref("남성", "남", "alias")]}
EXAMPLE_5 = full_query("req-ex5", "남성 제외하고 숏패딩이랑 반팔 니트 원피스 보여줘", items=[
    query_item(cat("숏패딩", "아우터", "패딩", "llm"), gender=_NOT_MEN,
               include={"body_length": [ref("숏패딩", "크롭", "approx")]}),
    query_item(cat("원피스", "원피스", "드레스", "alias"), item_id="i2", gender=_NOT_MEN,
               include={"material": [ref("니트", "니트", "exact")], "sleeve_length": [ref("반팔", "반팔", "exact")]}),
])

# Example 6: "블랙 부츠랑 셔츠 보여줘" - 부츠 is out of scope and keeps its own 블랙
EXAMPLE_6 = full_query("req-ex6", "블랙 부츠랑 셔츠 보여줘",
                       items=[query_item(cat("셔츠", "상의", "셔츠", "exact"))],
                       out_of_scope=[{"raw": "부츠", "reason": CATEGORY_REASON, "conditions_raw": ["블랙"]}])

# Example 7: "아우터는 빼고 블랙으로" - no item named: category null = 상의·하의·원피스 중 블랙
EXAMPLE_7 = full_query("req-ex7", "아우터는 빼고 블랙으로", items=[
    query_item(None, excluded=[cat("아우터", "아우터", None, "exact")], include={"color": [ref("블랙", "블랙", "exact")]}),
])

CONTRACT_EXAMPLES = {
    "section 2 shape": SECTION_2_SHAPE,
    "example 1": EXAMPLE_1,
    "example 2": EXAMPLE_2,
    "example 3": EXAMPLE_3,
    "example 4": EXAMPLE_4,
    "example 5": EXAMPLE_5,
    "example 6": EXAMPLE_6,
    "example 7": EXAMPLE_7,
}

# Keys that express producer-side policy rather than shape; checked separately in
# test_every_object_is_closed_and_fully_required, so the TrendQuery comparison shows only shape changes.
POLICY_KEYS = {"additionalProperties", "required", "default", "minLength", "$schema"}

# Every shape difference between trend's TrendQuery snapshot and StructuredQuery 1.0 at the document level.
# (kind, path) -> reason. Since the per-item grouping (2026-11-11) the whole `requested` block and the
# top-level `unsupported` list are replaced, so the finer differences inside an item (match on every value,
# attribute include/exclude per kind, gender lists, polarity, closed unsupported types, major never null)
# are pinned by the model tests above instead of by this comparison. Any other difference fails.
_TERM_VALUE = "properties.trend_terms.properties.style_names.items.properties.value"
INTENDED_DIFFERENCES = {
    ("added", "properties.schema_version"): "section 7: version",
    ("removed", "properties.requested"): "section 7: conditions grouped per item",
    ("removed", "properties.unsupported"): "section 7: unsupported conditions inside each item",
    ("added", "properties.items"): "section 7: conditions grouped per item",
    ("added", "properties.out_of_scope_items"): "section 7: items outside the standard categories",
    ("removed", _TERM_VALUE + ".anyOf"): "tightening: no vocabulary for trend names yet, so value is always null",
    ("added", _TERM_VALUE + ".type"): "tightening: no vocabulary for trend names yet, so value is always null",
    ("removed", "properties.trend_terms.properties.season"): "section 7: several seasons",
    ("added", "properties.trend_terms.properties.seasons"): "section 7: several seasons",
    ("removed", "properties.trend_terms.properties.item_terms"): "section 7: item_terms renamed",
    ("added", "properties.trend_terms.properties.product_model_names"): "section 7: item_terms renamed",
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
        cls.models = importlib.import_module("fashion_planner_orchestrator.query.models")

    def test_contract_examples_validate_and_round_trip(self):
        for name, document in CONTRACT_EXAMPLES.items():
            with self.subTest(example=name):
                parsed = self.models.StructuredQuery.model_validate(document)
                self.assertEqual(parsed.model_dump(mode="json"), document)

    def test_minimal_document_is_emitted_with_every_field(self):
        query = self.models.StructuredQuery(
            request_id="r", original_query="q",
            trend_terms={"seasons": [self.models.SeasonRequest.default_next(date(2026, 11, 10))]},
        )
        self.assertEqual(query.model_dump(mode="json"), full_query("r", "q"))
        self.assertEqual(self.models.QueryItem(item_id="i1").model_dump(mode="json"), query_item())
        self.assertEqual(query.schema_version, self.models.SCHEMA_VERSION)

    def test_package_exports_the_contract(self):
        package = importlib.import_module("fashion_planner_orchestrator.query")
        self.assertIs(package.StructuredQuery, self.models.StructuredQuery)
        self.assertEqual(package.SCHEMA_VERSION, "1.0.0")
        self.assertIs(package.TrendQuery, importlib.import_module("fashion_planner_orchestrator.query.trend_query").TrendQuery)


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuredQueryValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("fashion_planner_orchestrator.query.models")
        cls.ValidationError = importlib.import_module("pydantic").ValidationError

    def assert_invalid(self, document):
        with self.assertRaises(self.ValidationError):
            self.models.StructuredQuery.model_validate(document)

    def with_attribute(self, side, kind, value_ref):
        document = copy.deepcopy(EXAMPLE_3)
        document["items"][0]["attributes"][side][kind] = [value_ref]
        return document

    # --- items -----------------------------------------------------------------------------------

    def test_conditions_belong_to_an_item(self):
        # the flat shape of 2026-11-10 is gone: no top-level `requested` / `unsupported`
        for key, value in (("requested", {}), ("unsupported", [])):
            with self.subTest(key=key):
                document = copy.deepcopy(EXAMPLE_1)
                document[key] = value
                self.assert_invalid(document)
        self.assertEqual(tuple(self.models.QueryItem.model_fields),
                         ("item_id", "category", "excluded_categories", "gender", "attributes", "unsupported"))

    def test_item_ids_follow_the_item_order(self):
        # "i1", "i2", ... so a per-item result can be joined back
        self.assertEqual([i["item_id"] for i in EXAMPLE_5["items"]], ["i1", "i2"])
        for ids in (["i2", "i1"], ["i1", "i1"], ["i0", "i1"], ["1", "2"], ["i1", None]):
            with self.subTest(ids=ids):
                document = copy.deepcopy(EXAMPLE_5)
                for entry, item_id in zip(document["items"], ids):
                    entry["item_id"] = item_id
                self.assert_invalid(document)
        missing = copy.deepcopy(EXAMPLE_1)
        del missing["items"][0]["item_id"]
        self.assert_invalid(missing)

    def test_an_item_without_a_category_means_every_category(self):
        # category null + excluded categories, never rewritten into the remaining categories
        parsed = self.models.StructuredQuery.model_validate(EXAMPLE_7)
        self.assertIsNone(parsed.items[0].category)
        self.assertEqual([c.major for c in parsed.items[0].excluded_categories], ["아우터"])

    def test_out_of_scope_items_keep_their_own_conditions_as_text(self):
        # an out-of-scope item
        self.assertEqual(tuple(self.models.OutOfScopeItem.model_fields), ("raw", "reason", "conditions_raw"))
        for bad in ({"raw": "부츠", "conditions_raw": []}, {"raw": "", "reason": "x", "conditions_raw": []},
                    {"raw": "부츠", "reason": "x", "conditions_raw": [""]},
                    {"raw": "부츠", "reason": "x", "conditions_raw": [], "category": None}):
            with self.subTest(entry=bad):
                document = copy.deepcopy(EXAMPLE_6)
                document["out_of_scope_items"] = [bad]
                self.assert_invalid(document)

    def test_items_may_be_empty_only_as_a_list(self):
        # every named item out of scope -> items == [] (never null)
        document = full_query("r", "모자랑 가방", out_of_scope=[
            {"raw": "모자", "reason": CATEGORY_REASON, "conditions_raw": []}])
        self.models.StructuredQuery.model_validate(document)
        document["items"] = None
        self.assert_invalid(document)

    # --- values and the closed vocabulary -------------------------------------------------------

    def test_attribute_kinds_and_values_follow_the_vocabulary(self):
        self.assertEqual(tuple(self.models.AttributeValues.model_fields), cvocab.ATTRIBUTE_TYPES)
        self.assertEqual(tuple(self.models.ATTRIBUTE_REF_TYPES), cvocab.ATTRIBUTE_TYPES)
        self.assertEqual(cvocab.ATTRIBUTE_TYPES[:5], vocab.ATTRIBUTE_TYPES)
        self.assertEqual(len(vocab.ATTRIBUTE_VALUES["detail"]), 61)
        for kind, ref_type in self.models.ATTRIBUTE_REF_TYPES.items():
            with self.subTest(kind=kind):
                enum = ref_type.model_json_schema()["properties"]["value"]["anyOf"][0]["enum"]
                self.assertEqual(tuple(enum), cvocab.ATTRIBUTE_VALUES[kind])
                self.assertEqual(tuple(ref_type.model_fields), ("raw", "value", "match"))

    def test_length_values_are_trends_own(self):
        # trend docs/schemas/vocabulary.md §6 (BodyLength 9, SleeveLength 5), no new values
        self.assertEqual(cvocab.ATTRIBUTE_VALUES["body_length"],
                         ("크롭", "노멀", "하프", "롱", "미니", "니렝스", "미디", "발목", "맥시"))
        self.assertEqual(cvocab.ATTRIBUTE_VALUES["sleeve_length"], ("민소매", "반팔", "캡", "7부소매", "긴팔"))
        self.models.StructuredQuery.model_validate(self.with_attribute(
            "include", "body_length", ref("미니", "미니", "exact")))
        self.assert_invalid(self.with_attribute("include", "body_length", ref("반팔", "반팔", "exact")))

    def test_gender_is_include_exclude_lists(self):
        # "남성 말고" = exclude [남]; both lists always present, never null (per item)
        document = copy.deepcopy(EXAMPLE_3)
        document["items"][0]["gender"] = {"include": [], "exclude": [ref("남성", "남", "alias")]}
        parsed = self.models.StructuredQuery.model_validate(document)
        self.assertEqual(parsed.model_dump(mode="json"), document)
        for bad in (None, ref("여성", "여", "alias"), {"include": None, "exclude": []},
                    {"include": [], "exclude": [], "values": ["여"]}):
            with self.subTest(gender=bad):
                document["items"][0]["gender"] = bad
                self.assert_invalid(document)

    def test_unsupported_type_is_one_of_nine(self):
        self.assertEqual(cvocab.UNSUPPORTED_TYPES,
                         ("brand", "category", "attribute", "abstract", "price", "market", "channel", "age", "other"))
        for type_ in ("gender", "브랜드", "brand_name", ""):
            with self.subTest(type=type_):
                document = copy.deepcopy(EXAMPLE_4)
                document["items"][0]["unsupported"][0]["type"] = type_
                self.assert_invalid(document)

    def test_value_outside_the_kind_vocabulary_is_rejected(self):
        cases = [
            ("color", "핑크색"),        # not a standard value
            ("color", "브이넥"),        # a standard value of another kind
            ("detail", "블랙"),
            ("fit", "슬림"),            # an alias, not the standard value
        ]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                self.assert_invalid(self.with_attribute("include", kind, ref(value, value, "exact")))

    def test_detail_holds_neckline_and_collar_values(self):
        for value in ("셔링", "터틀넥", "셔츠칼라", "후드"):
            with self.subTest(value=value):
                self.models.StructuredQuery.model_validate(self.with_attribute("exclude", "detail", ref(value, value, "exact")))

    def test_match_is_none_exactly_when_value_is_null(self):
        self.models.StructuredQuery.model_validate(self.with_attribute("include", "color", ref("연두", None, "none")))
        for bad in (ref("연두", "그린", "none"), ref("연두", None, "llm"), ref("연두", "그린", "guess")):
            with self.subTest(ref=bad):
                self.assert_invalid(self.with_attribute("include", "color", bad))

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
        document["items"][0]["attributes"] = {"exclude": {"color": [ref("블랙", "블랙", "exact")]}}
        dumped = self.models.StructuredQuery.model_validate(document).model_dump(mode="json")
        self.assertEqual(dumped, EXAMPLE_1)

    def test_null_or_unknown_attribute_keys_are_rejected(self):
        null_key = copy.deepcopy(EXAMPLE_1)
        null_key["items"][0]["attributes"]["include"]["color"] = None
        self.assert_invalid(null_key)
        unknown_key = copy.deepcopy(EXAMPLE_1)
        unknown_key["items"][0]["attributes"]["include"]["length"] = []
        self.assert_invalid(unknown_key)
        old_shape = copy.deepcopy(EXAMPLE_1)
        old_shape["items"][0]["attributes"] = [{"type": "color", "raw": "블랙", "value": "블랙"}]
        self.assert_invalid(old_shape)

    def test_empty_lists_are_never_null(self):
        for path in (("items",), ("out_of_scope_items",), ("missing_fields",), ("trend_terms", "style_names"),
                     ("items", 0, "excluded_categories"), ("items", 0, "unsupported")):
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
            {"raw": "가방", "major": None, "minor": None, "match": "none"},  # goes out of scope instead
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

        def item(d):
            d["items"][0]["note"] = "x"

        def category(d):
            d["items"][0]["category"]["confidence"] = 0.9

        def attribute(d):
            d["items"][0]["attributes"]["exclude"]["color"][0]["type"] = "color"

        def unsupported(d):
            d["items"][0]["unsupported"][0]["note"] = "x"

        def out_of_scope(d):
            d["out_of_scope_items"] = [{"raw": "부츠", "reason": "x", "conditions_raw": [], "type": "category"}]

        def season(d):
            d["trend_terms"]["seasons"][0]["month"] = 3

        for mutate in (top, item, category, attribute, unsupported, out_of_scope, season):
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
                document["items"][0]["unsupported"][0]["polarity"] = polarity
                self.assert_invalid(document)
        document = copy.deepcopy(EXAMPLE_4)
        del document["items"][0]["unsupported"][0]["polarity"]
        self.assert_invalid(document)

    def test_seasons_are_a_non_empty_list(self):
        # several seasons; a query naming none still carries the default_next one
        two = copy.deepcopy(EXAMPLE_3)
        two["trend_terms"]["seasons"] = [{"year": 2027, "season": "SS", "raw": "올봄", "origin": "user_requested"},
                                          {"year": 2027, "season": "FW", "raw": "가을", "origin": "user_requested"}]
        self.models.StructuredQuery.model_validate(two)
        for bad in ([], None, {"year": 2027, "season": "SS", "raw": None, "origin": "default_next"}):
            with self.subTest(seasons=bad):
                document = copy.deepcopy(EXAMPLE_3)
                document["trend_terms"]["seasons"] = bad
                self.assert_invalid(document)
        old = copy.deepcopy(EXAMPLE_3)
        old["trend_terms"]["item_terms"] = []  # renamed to product_model_names
        self.assert_invalid(old)

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
        self.assert_invalid(self.with_attribute("include", "fit", ref("", "루즈", "exact")))


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuredQuerySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = importlib.import_module("fashion_planner_orchestrator.query.models")
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
        bad["items"][0]["attributes"]["include"]["color"][0]["value"] = "버터옐로우"
        del bad["items"][0]["attributes"]["exclude"]["fit"]
        self.assertEqual(len(list(validator.iter_errors(bad))), 2)

    @unittest.skipUnless(SNAPSHOT_PATH.exists(), "contract snapshot not present in this checkout")
    def test_differences_from_the_trend_query_snapshot_are_the_intended_ones(self):
        snapshot = load_snapshot()["json_schema"]
        old = normalize(snapshot, snapshot["$defs"], POLICY_KEYS)
        new = normalize(self.schema, self.schema["$defs"], POLICY_KEYS)
        self.assertEqual(sorted(schema_diff(old, new)), sorted(INTENDED_DIFFERENCES))


if __name__ == "__main__":
    unittest.main()
