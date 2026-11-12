"""The StructuredQuery path (query/structured.py, prompt v6: flat draft v4 grouped per item by code) and its
datasets. No model call.

The rule tests write drafts in the grouped draft v3 shape (easier to read) and `run_path` feeds them to the
current draft_v4 prompt through draft_v4.from_draft_v3, so the grouping code runs in every test.

The path is NOT wired into the UI server or `structure_query` yet (plan U4); these tests also pin that
the running path still uses prompt v1. Words in the rule tests are not from the evaluation data (W-10).
"""

import importlib
import importlib.util
import json
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, ROOT, SKIP_REASON

DATA_PATH = ROOT / "data" / "user_queries.v3.json"
HOLDOUT_PATH = ROOT / "data" / "user_queries.holdout.v2.json"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def c(raw, kind, suggestion, polarity="include"):
    return {"raw": raw, "kind": kind, "polarity": polarity, "suggestion": suggestion}


def item(raw, suggestion, conditions=(), unsupported=()):
    return {"raw": raw, "suggestion": suggestion, "conditions": list(conditions), "unsupported": list(unsupported)}


def draft(items=(), common=(), common_unsupported=(), seasons=()):
    return {"seasons": list(seasons), "style_names": [], "product_model_names": [], "items": list(items),
            "common": {"conditions": list(common), "unsupported": list(common_unsupported)}}


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class StructuredPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.structured = importlib.import_module("vogue_orchestrator.query.structured")
        cls.errors = importlib.import_module("vogue_orchestrator.query.errors")
        cls.config = importlib.import_module("vogue_orchestrator.query.config")
        cls.draft_v4 = importlib.import_module("vogue_orchestrator.query.draft_v4")

    def run_path(self, produced, query, as_written=False, **kwargs):
        if not as_written and isinstance(produced, dict) and "common" in produced and "prompt_version" not in kwargs:
            produced = self.draft_v4.from_draft_v3(produced)
        return self.structured.structure_query_v2(
            query, request_id="r-1", draft_provider=lambda bundle: produced, today=date(2026, 11, 10), **kwargs)

    def items_of(self, result):
        return result.structured_query.model_dump(mode="json")["items"]

    def test_running_path_is_still_v1(self):
        self.assertEqual(self.config.CONFIG.prompt_version, "v1")

    def test_conditions_stay_with_their_item_and_common_ones_are_copied(self):
        # IT-1, IT-2, IT-3: "니트" belongs to the vest only; the gender exclusion is copied into both items
        query = "남성 말고 니트 베스트랑 와이드 조거팬츠"
        result = self.run_path(draft(items=[
            item("베스트", "베스트", [c("니트", "material", "니트")]),
            item("조거팬츠", "조거팬츠", [c("와이드", "fit", "와이드")]),
        ], common=[c("남성", "gender", "남", "exclude")]), query)
        items = self.items_of(result)
        self.assertEqual([i["category"]["minor"] for i in items], ["베스트", "조거팬츠"])
        self.assertEqual([[r["value"] for r in i["attributes"]["include"]["material"]] for i in items], [["니트"], []])
        self.assertEqual([[r["value"] for r in i["attributes"]["include"]["fit"]] for i in items], [[], ["와이드"]])
        for i in items:
            self.assertEqual(i["gender"], {"include": [], "exclude": [{"raw": "남성", "value": "남", "match": "alias"}]})
        self.assertEqual(result.prompt_version, "query-structuring-prompt/v6")
        self.assertEqual(result.warnings, ())

    def test_gender_before_the_first_item_goes_to_every_item(self):
        # IT-9: "위민즈 점퍼랑 레깅스" = 위민즈 점퍼 + 위민즈 레깅스, even when the model put it on the first item only
        gender = lambda items: [[r["value"] for r in i["gender"]["include"]] for i in items]
        result = self.run_path(draft(items=[item("점퍼", "점퍼", [c("위민즈", "gender", "여")]), item("레깅스", "레깅스")]),
                               "위민즈 점퍼랑 레깅스")
        self.assertEqual(gender(self.items_of(result)), [["여"], ["여"]])
        self.assertIn("leading_gender_shared", result.warnings)
        # another item names its own gender -> each keeps its own
        result = self.run_path(draft(items=[item("점퍼", "점퍼", [c("맨즈", "gender", "남")]),
                                            item("레깅스", "레깅스", [c("위민즈", "gender", "여")])]), "맨즈 점퍼랑 위민즈 레깅스")
        self.assertEqual(gender(self.items_of(result)), [["남"], ["여"]])
        self.assertNotIn("leading_gender_shared", result.warnings)
        # a gender before a LATER item stays with that item (IT-2)
        result = self.run_path(draft(items=[item("점퍼", "점퍼"), item("레깅스", "레깅스", [c("맨즈", "gender", "남")])]),
                               "점퍼랑 맨즈 레깅스")
        self.assertEqual(gender(self.items_of(result)), [[], ["남"]])
        # the exclusion form follows the same rule
        result = self.run_path(draft(items=[item("점퍼", "점퍼", [c("맨즈", "gender", "남", "exclude")]), item("레깅스", "레깅스")]),
                               "맨즈 말고 점퍼랑 레깅스")
        self.assertEqual([[r["value"] for r in i["gender"]["exclude"]] for i in self.items_of(result)], [["남"], ["남"]])

    def test_items_follow_the_query_order(self):
        # IT-4: items[0] is the item named first, whatever order the model wrote them in
        query = "점퍼랑 레깅스 보여줘"
        items = self.items_of(self.run_path(draft(items=[item("레깅스", "레깅스"), item("점퍼", "점퍼")]), query))
        self.assertEqual([i["category"]["minor"] for i in items], ["점퍼", "레깅스"])
        self.assertEqual([i["item_id"] for i in items], ["i1", "i2"])  # IT-8

    def test_no_item_named_gives_one_item_over_every_category(self):
        # IT-6: "상의는 빼고 그린" = 하의·아우터·원피스 중 그린; the exclusion is not rewritten
        items = self.items_of(self.run_path(draft(common=[
            c("상의", "category", "상의", "exclude"), c("그린", "color", "그린")]), "상의는 빼고 그린"))
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["category"])
        self.assertEqual(items[0]["excluded_categories"], [{"raw": "상의", "major": "상의", "minor": None, "match": "exact"}])
        self.assertEqual(items[0]["attributes"]["include"]["color"], [{"raw": "그린", "value": "그린", "match": "exact"}])

    def test_out_of_scope_items_keep_their_conditions_and_never_widen_the_search(self):
        # IT-7: 가방 is not an item; its own "레더" stays with it; the common condition is copied to it too
        query = "레더 가방이랑 블라우스, 둘 다 브라운으로"
        result = self.run_path(draft(items=[
            item("가방", None, [c("레더", "material", "가죽")]), item("블라우스", "블라우스")],
            common=[c("브라운", "color", "브라운")]), query)
        sq = result.structured_query.model_dump(mode="json")
        self.assertEqual([i["category"]["minor"] for i in sq["items"]], ["블라우스"])
        self.assertEqual(sq["items"][0]["attributes"]["include"]["material"], [])
        self.assertEqual([r["value"] for r in sq["items"][0]["attributes"]["include"]["color"]], ["브라운"])
        self.assertEqual([(o["raw"], o["conditions_raw"]) for o in sq["out_of_scope_items"]], [("가방", ["레더", "브라운"])])

    def test_only_out_of_scope_items_still_give_one_item_over_every_category(self):
        # IT-7′ (2026-11-12): the season must still reach trend; 벨트 is only an approx detail ("띠") in the
        # dictionary, so IT-10 does not turn it into a condition. Its own "블랙" never widens the item.
        result = self.run_path(draft(items=[item("벨트", None, [c("블랙", "color", "블랙")])],
                                     seasons=[{"year": 2027, "season": "SS", "raw": "2027SS"}]), "2027SS 블랙 벨트 보여줘")
        sq = result.structured_query.model_dump(mode="json")
        self.assertEqual(len(sq["items"]), 1)
        self.assertIsNone(sq["items"][0]["category"])
        self.assertEqual(sq["items"][0]["attributes"]["include"]["color"], [])
        self.assertEqual([(o["raw"], o["conditions_raw"]) for o in sq["out_of_scope_items"]], [("벨트", ["블랙"])])
        self.assertEqual(sq["trend_terms"]["seasons"][0]["raw"], "2027SS")
        self.assertIn("only_out_of_scope_items", result.warnings)
        self.assertNotIn("item_word_used_as_condition", result.warnings)

    def test_an_item_word_that_is_an_attribute_value_becomes_an_item_over_every_category(self):
        # IT-10 (2026-11-12): "반팔" alone is a sleeve length, not a category -> category null + sleeve_length
        result = self.run_path(draft(items=[item("반팔", "반팔")], common=[c("여성", "gender", "여")],
                                     seasons=[{"year": 2027, "season": "SS", "raw": "2027SS"}]),
                               "2027SS 여성 반팔 제작하고 싶어")
        sq = result.structured_query.model_dump(mode="json")
        self.assertEqual(sq["out_of_scope_items"], [])
        (only,) = sq["items"]
        self.assertIsNone(only["category"])
        self.assertEqual(only["attributes"]["include"]["sleeve_length"], [{"raw": "반팔", "value": "반팔", "match": "exact"}])
        self.assertEqual([g["value"] for g in only["gender"]["include"]], ["여"])
        self.assertIn("item_word_used_as_condition", result.warnings)
        self.assertNotIn("only_out_of_scope_items", result.warnings)

    def test_an_attribute_item_word_keeps_its_own_item_next_to_a_real_one(self):
        # IT-10: "반팔이랑 스커트" -> the sleeve length stays on its own item; the skirt gets no sleeve condition
        items = self.items_of(self.run_path(draft(items=[item("반팔", None), item("스커트", "스커트")]), "반팔이랑 스커트"))
        self.assertEqual([i["category"] and i["category"]["minor"] for i in items], [None, "스커트"])
        self.assertEqual([v["value"] for v in items[0]["attributes"]["include"]["sleeve_length"]], ["반팔"])
        self.assertEqual(items[1]["attributes"]["include"]["sleeve_length"], [])

    def test_an_item_word_with_several_attribute_kinds_follows_the_priority(self):
        # IT-10: exact before alias, then sleeve_length > body_length > ... > detail
        cases = {"민소매": ("sleeve_length", "민소매"), "체크": ("pattern", "체크"), "롱": ("body_length", "롱")}
        for word, (kind, value) in cases.items():
            with self.subTest(word=word):
                (only,) = self.items_of(self.run_path(draft(items=[item(word, None)]), f"{word} 보여줘"))
                found = {k: [v["value"] for v in vs] for k, vs in only["attributes"]["include"].items() if vs}
                self.assertEqual(found, {kind: [value]})

    def test_one_expression_with_two_categories_gives_two_items(self):
        # OQ-10′ on items: the spaced "셔츠 원피스" names 셔츠 and 드레스 -> two items with the same conditions
        items = self.items_of(self.run_path(draft(items=[item("셔츠 원피스", "드레스", [c("네이비", "color", "네이비")])]),
                                            "네이비 셔츠 원피스"))
        self.assertEqual([i["category"]["minor"] for i in items], ["셔츠", "드레스"])
        self.assertEqual([[r["value"] for r in i["attributes"]["include"]["color"]] for i in items], [["네이비"], ["네이비"]])

    def test_duplicates_are_removed_per_item_and_reasons_come_from_the_config(self):
        query = "블랙 검정 자라 자 라 점퍼"
        items = self.items_of(self.run_path(draft(items=[item("점퍼", "점퍼", [
            c("블랙", "color", "블랙"), c("검정", "color", "블랙")],
            [{"type": "brand", "raw": "자라", "polarity": "exclude"}, {"type": "brand", "raw": "자 라", "polarity": "exclude"}])]),
            query))
        self.assertEqual(items[0]["attributes"]["include"]["color"], [{"raw": "블랙", "value": "블랙", "match": "exact"}])
        self.assertEqual(items[0]["unsupported"], [{"type": "brand", "raw": "자라", "reason": "Product 데이터는 Musinsa뿐",
                                                    "polarity": "exclude"}])

    def test_errors_use_fixed_codes(self):
        with self.assertRaises(self.errors.QueryStructuringError) as ctx:
            self.run_path("not json", "셔츠")
        self.assertEqual(ctx.exception.code, self.errors.DRAFT_NOT_JSON)
        for bad in (
            # a structural error (unknown key) is never repaired; a wrong label would be (OQ-12′)
            draft(items=[item("셔츠", "셔츠", [{"raw": "x", "kind": "color", "polarity": "include", "match": "exact"}])]),
            {"items": [{"raw": "셔츠", "suggestion": "셔츠", "conditions": [], "unsupported": []}]},  # nested v3 item
            {"season": None, "conditions": []},  # the flat draft v2 shape is not draft v4
        ):
            with self.subTest(draft=bad):
                with self.assertRaises(self.errors.QueryStructuringError) as ctx:
                    self.run_path(bad, "셔츠")
                self.assertEqual(ctx.exception.code, self.errors.DRAFT_INVALID)

    def test_wrong_labels_are_repaired_in_every_block(self):
        # decision OQ-12′ (words not from the dataset)
        query = "고급스러운 셔츠, 우아한 느낌"
        result = self.run_path(draft(
            items=[item("셔츠", "셔츠", [c("고급스러운", "abstract", None)])],
            common=[c("우아한 느낌", "mood", None)]), query)
        items = self.items_of(result)
        self.assertEqual([(u["type"], u["raw"]) for u in items[0]["unsupported"]],
                         [("abstract", "고급스러운"), ("other", "우아한 느낌")])
        self.assertIn("draft_kind_moved_to_unsupported", result.warnings)
        self.assertIn("draft_unknown_kind", result.warnings)

    def test_misplaced_categories_become_items_or_exclusions(self):
        # label level (W-10): an included category written as a condition is an item; an included category in
        # `unsupported` is looked up with the dictionary only (OQ-13′); an excluded one becomes an exclusion
        query = "베스트 점퍼, 하의 말고 와이드팬츠"
        result = self.run_path(draft(
            items=[item("베스트", "베스트")],
            common=[c("점퍼", "category", "점퍼")],
            common_unsupported=[{"type": "category", "raw": "하의", "polarity": "exclude"},
                                {"type": "category", "raw": "와이드팬츠", "polarity": "include"}]), query)
        sq = result.structured_query.model_dump(mode="json")
        self.assertEqual([i["category"]["minor"] for i in sq["items"]], ["베스트", "점퍼"])
        for i in sq["items"]:
            self.assertEqual([e["major"] for e in i["excluded_categories"]], ["하의"])
        self.assertEqual([o["raw"] for o in sq["out_of_scope_items"]], ["와이드팬츠"])  # dictionary does not know it
        self.assertIn("category_condition_moved_to_item", result.warnings)
        self.assertIn("unsupported_category_resolved", result.warnings)

    def test_several_seasons_are_kept_in_query_order(self):
        # SQ-2: year rule A-1 for each, the same season twice kept once; none named -> default_next
        result = self.run_path(draft(items=[item("점퍼", "점퍼")], seasons=[
            {"year": None, "season": "FW", "raw": "이번 겨울"}, {"year": 2027, "season": "SS", "raw": "2027 봄"},
            {"year": 2027, "season": "SS", "raw": "27년 여름"}]), "이번 겨울이랑 2027 봄, 27년 여름 점퍼")
        seasons = result.structured_query.model_dump(mode="json")["trend_terms"]["seasons"]
        self.assertEqual([(s["year"], s["season"], s["raw"]) for s in seasons], [(2027, "FW", "이번 겨울"), (2027, "SS", "2027 봄")])
        self.assertIn("season_year_inferred", result.warnings)
        none = self.run_path(draft(items=[item("점퍼", "점퍼")]), "점퍼").structured_query.model_dump(mode="json")
        self.assertEqual([s["origin"] for s in none["trend_terms"]["seasons"]], ["default_next"])

    def test_a_prompt_without_items_is_refused(self):
        for version in ("v1", "v3"):
            with self.subTest(version=version):
                with self.assertRaises(self.config.QueryConfigError):
                    self.run_path({}, "셔츠", prompt_version=version)

    def test_the_archived_nested_prompt_still_runs(self):
        # prompt v4 (draft_v3): the model writes the grouped draft itself; kept so its reports can be replayed
        result = self.run_path(draft(items=[item("점퍼", "점퍼", [c("네이비", "color", "네이비")])]), "네이비 점퍼",
                               prompt_version="v4")
        self.assertEqual(result.prompt_version, "query-structuring-prompt/v4")
        self.assertEqual(self.items_of(result)[0]["attributes"]["include"]["color"][0]["value"], "네이비")

    # --- FL-1: flat draft v4, grouped by the `item` field (words not from the dataset, W-10) ---------------

    def flat(self, items, conditions=(), unsupported=()):
        return {"seasons": [], "style_names": [], "product_model_names": [],
                "items": [{"raw": raw, "suggestion": suggestion} for raw, suggestion in items],
                "conditions": list(conditions), "unsupported": list(unsupported)}

    @staticmethod
    def fc(raw, kind, suggestion, item, polarity="include"):
        return {"raw": raw, "kind": kind, "polarity": polarity, "suggestion": suggestion, "item": item}

    def test_flat_conditions_go_to_the_item_they_name(self):
        query = "코듀로이 점퍼랑 레깅스, 둘 다 네이비"
        result = self.run_path(self.flat([("점퍼", "점퍼"), ("레깅스", "레깅스")], [
            self.fc("코듀로이", "material", "코듀로이", "점퍼"), self.fc("네이비", "color", "네이비", None)]), query)
        items = self.items_of(result)
        self.assertEqual([[r["value"] for r in i["attributes"]["include"]["material"]] for i in items], [["코듀로이"], []])
        self.assertEqual([[r["value"] for r in i["attributes"]["include"]["color"]] for i in items], [["네이비"], ["네이비"]])
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.draft.model_dump(mode="json")["conditions"][0]["item"], "점퍼")  # what the model wrote

    def test_an_item_reference_is_matched_loosely_or_becomes_query_wide(self):
        query = "코듀로이 패딩 점퍼랑 레깅스"
        # "패딩 점퍼" contains the item raw "점퍼" -> that item
        result = self.run_path(self.flat([("점퍼", "점퍼"), ("레깅스", "레깅스")], [
            self.fc("코듀로이", "material", "코듀로이", "패딩 점퍼")]), query)
        self.assertEqual([len(i["attributes"]["include"]["material"]) for i in self.items_of(result)], [1, 0])
        self.assertNotIn("condition_item_unknown", result.warnings)
        # a name that is no item -> query-wide, kept, with a warning
        result = self.run_path(self.flat([("점퍼", "점퍼"), ("레깅스", "레깅스")], [
            self.fc("코듀로이", "material", "코듀로이", "가디건")]), query)
        self.assertEqual([len(i["attributes"]["include"]["material"]) for i in self.items_of(result)], [1, 1])
        self.assertIn("condition_item_unknown", result.warnings)

    def test_the_same_item_twice_is_told_apart_by_position(self):
        query = "코듀로이 점퍼랑 퀼팅 점퍼"
        result = self.run_path(self.flat([("점퍼", "점퍼"), ("점퍼", "점퍼")], [
            self.fc("코듀로이", "material", "코듀로이", "점퍼"), self.fc("퀼팅", "detail", None, "점퍼")]), query)
        items = self.items_of(result)
        self.assertEqual([[r["raw"] for r in i["attributes"]["include"]["material"]] for i in items], [["코듀로이"], []])
        self.assertEqual([[r["raw"] for r in i["attributes"]["include"]["detail"]] for i in items], [[], ["퀼팅"]])

    def test_a_repaired_label_keeps_its_item(self):
        query = "고급스러운 점퍼랑 레깅스"
        result = self.run_path(self.flat([("점퍼", "점퍼"), ("레깅스", "레깅스")], [
            self.fc("고급스러운", "abstract", None, "점퍼")]), query)
        self.assertEqual([[u["raw"] for u in i["unsupported"]] for i in self.items_of(result)], [["고급스러운"], []])
        self.assertIn("draft_kind_moved_to_unsupported", result.warnings)


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class ContractExamplesAreProducedByTheCodeTests(unittest.TestCase):
    """The contract examples with several items (contracts/agents/vogue-orchestrator/output/structured-query.md §6) come out of the assembly."""

    @classmethod
    def setUpClass(cls):
        cls.structured = importlib.import_module("vogue_orchestrator.query.structured")
        cls.draft_v3 = importlib.import_module("vogue_orchestrator.query.draft_v3")
        cls.examples = importlib.import_module("test_query_structured_models")

    def assemble(self, produced, query, request_id):
        parsed = self.draft_v3.QueryDraftV3.model_validate(produced)
        return self.structured.assemble_structured_query(
            parsed, request_id=request_id, original_query=query, today=date(2026, 11, 10)).structured_query

    def test_examples(self):
        cases = {
            "example 4": (draft(common=[c("가방", "category", None, "exclude"), c("신발", "category", None, "exclude")],
                                common_unsupported=[{"type": "brand", "raw": "자라", "polarity": "exclude"}]),
                          self.examples.EXAMPLE_4),
            "example 5": (draft(items=[item("숏패딩", "패딩", [c("숏패딩", "body_length", "크롭")]),
                                       item("원피스", "드레스", [c("반팔", "sleeve_length", "반팔"), c("니트", "material", "니트")])],
                                common=[c("남성", "gender", "남", "exclude")]), self.examples.EXAMPLE_5),
            "example 6": (draft(items=[item("부츠", None, [c("블랙", "color", "블랙")]), item("셔츠", "셔츠")]),
                          self.examples.EXAMPLE_6),
            "example 7": (draft(common=[c("아우터", "category", "아우터", "exclude"), c("블랙", "color", "블랙")]),
                          self.examples.EXAMPLE_7),
        }
        for name, (produced, expected) in cases.items():
            with self.subTest(example=name):
                sq = self.assemble(produced, expected["original_query"], expected["request_id"])
                self.assertEqual(sq.model_dump(mode="json"), expected)


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        cls.holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
        cls.evaluator = _load_script("evaluate_query_structuring")
        cls.prompt = importlib.import_module("vogue_orchestrator.query.prompt")

    def test_data_files_are_the_builder_output(self):
        # Fails when the labels or the code changed but the build scripts were not run.
        self.assertEqual(DATA_PATH.read_text(encoding="utf-8"), _load_script("build_query_dataset_v3").render())
        self.assertEqual(HOLDOUT_PATH.read_text(encoding="utf-8"), _load_script("build_query_holdout_v2").render())

    def test_query_texts_did_not_change_with_the_format(self):
        # W-11: only the labels were rewritten for the per-item format
        for old_name, new in (("user_queries.v2.json", self.data), ("user_queries.holdout.v1.json", self.holdout)):
            with self.subTest(old=old_name):
                old = json.loads((ROOT / "data" / old_name).read_text(encoding="utf-8"))
                self.assertEqual({i["id"]: i["query"] for i in old["items"]}, {i["id"]: i["query"] for i in new["items"]})

    def test_sets_are_unique_and_kept_apart_from_the_prompt_examples(self):
        dev = [i["query"] for i in self.data["items"]]
        held = [i["query"] for i in self.holdout["items"]]
        self.assertEqual((len(dev), len(set(dev)), len(held), len(set(held))), (30, 30, 20, 20))
        examples = {e["query"] for v in ("v1", "v2", "v3", "v4", "v5", "v6") for e in self.prompt.load_prompt(v).examples}
        self.assertEqual(set(dev) & examples, set())  # overlap would inflate accuracy
        self.assertEqual(set(held) & (set(dev) | examples), set())

    def test_code_part_reproduces_every_label(self):
        # v6: the labels written flat go through the grouping code; v4: the grouped labels as they are
        for version in ("v6", "v4"):
            for name, data, size in (("dev", self.data, 30), ("holdout", self.holdout, 20)):
                with self.subTest(dataset=name, prompt=version):
                    report = self.evaluator.run(data, "fixture", None, version)
                    self.assertEqual(report["summary"]["exact_with_match"], size,
                                     [r for r in report["items"] if not r["exact_with_match"]])

    def test_new_holdout_is_built_kept_apart_and_reproduced_by_the_code(self):
        # holdout v3 (2026-11-11): written by a separate agent that did not read prompt v4; the final check of
        # the next prompt (W-10). Compared in code only, so the main session never reads its queries.
        path = ROOT / "data" / "user_queries.holdout.v3.json"
        holdout = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path.read_text(encoding="utf-8"), _load_script("build_query_holdout_v3").render())
        queries = {i["query"] for i in holdout["items"]}
        self.assertEqual(len(queries), 20)
        earlier = set()
        for name in ("user_queries.v1.json", "user_queries.v2.json", "user_queries.v3.json",
                     "user_queries.holdout.v1.json", "user_queries.holdout.v2.json"):
            earlier |= {i["query"] for i in json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))["items"]}
        earlier |= {e["query"] for v in ("v1", "v2", "v3", "v4", "v5", "v6") for e in self.prompt.load_prompt(v).examples}
        self.assertEqual(len(queries & earlier), 0)
        for version in ("v6", "v4"):
            report = self.evaluator.run(holdout, "fixture", None, version)
            self.assertEqual(report["summary"]["exact_with_match"], 20, version)  # counts only, no query text

    def test_comparison_puts_a_condition_on_the_wrong_item_as_missing_and_extra(self):
        entry = next(i for i in self.data["items"] if i["id"] == "q029")  # 스키니 청바지랑 루즈핏 셔츠
        expected = self.evaluator.facts(entry["expected_structured_query"])
        produced = dict(expected)
        del produced[("fit", 1, "include", "루즈")]
        produced[("fit", 0, "include", "루즈")] = "llm"
        produced[("fit", 0, "include", "스키니")] = "alias"
        result = self.evaluator.compare(expected, produced)
        self.assertEqual(result["missing"], [["fit", 1, "include", "루즈"]])
        self.assertEqual(result["extra"], [["fit", 0, "include", "루즈"]])
        self.assertEqual(result["match_diff"], [["fit", 0, "include", "스키니", "exact", "alias"]])
        self.assertFalse(result["exact"])

    def test_brands_are_not_scored(self):
        # BR-1: a brand label stays in the data but is not compared; a brand written as a style name is not extra
        expected = {("category", 0, "item", "아우터", "점퍼"): "exact",
                    ("unsupported", 0, "brand", "include", "몽클레르"): None}
        produced = {("category", 0, "item", "아우터", "점퍼"): "exact",
                    ("style", "몽클레르스타일"): None, ("style", "고프코어"): None}
        result = self.evaluator.compare(*self.evaluator.checked(expected, produced))
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["extra"], [["style", "고프코어"]])


if __name__ == "__main__":
    unittest.main()
