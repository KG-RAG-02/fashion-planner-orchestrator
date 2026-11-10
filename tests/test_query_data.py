"""data/user_queries.v1.json: 30 expected user queries with silver drafts. Checks integrity, not LLM accuracy.

The v1 data set targets prompt v1 (draft_v1, archived). Its drafts are validated with legacy_v1.QueryDraft
and the v1 assembler. The data file must name a prompt version folder that exists; it does not have to
be the active version (plan §3: archived assets are kept to trace old results).
"""

import importlib
import json
import unittest
from datetime import date

from _query_support import HAS_PYDANTIC, ROOT, SKIP_REASON

DATA_FILE = ROOT / "data" / "user_queries.v1.json"
PROMPT_ROOT = ROOT / "prompts" / "query_structuring"
ALLOWED_TAGS = {
    "season_with_year", "season_without_year", "relative_season", "mixed_notation",
    "include", "exclude", "major_only", "minor", "multi_category", "all_categories_unsupported",
    "gender", "color", "material", "pattern", "detail", "fit", "style_name", "item_term",
    "unsupported_brand", "unsupported_price", "unsupported_channel", "unsupported_market", "unsupported_age",
    "unsupported_category", "unsupported_attribute", "abstract", "no_conditions", "prompt_injection", "ambiguous",
}
# Corrections the assembler may legitimately report for these drafts.
ALLOWED_WARNINGS = {"season_year_inferred", "no_conditions_extracted"}


def prompt_version_names():
    return sorted(p.name for p in PROMPT_ROOT.iterdir() if p.is_dir())


def load_dataset():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


class DatasetShapeTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset()
        self.items = self.dataset["items"]

    def test_thirty_items_with_unique_ids_and_queries(self):
        self.assertEqual(len(self.items), 30)
        self.assertEqual(len({i["id"] for i in self.items}), 30)
        self.assertEqual(len({i["query"] for i in self.items}), 30)

    def test_tags_and_label_status_are_from_the_documented_sets(self):
        for item in self.items:
            with self.subTest(id=item["id"]):
                self.assertLessEqual(set(item["tags"]), ALLOWED_TAGS)
                self.assertIn(item["label_status"], {"silver", "gold"})

    def test_every_raw_expression_is_copied_from_its_query(self):
        for item in self.items:
            draft = item["expected_draft"]
            raws = [draft["season"]["raw"]] if draft["season"] else []
            raws += [t["raw"] for key in ("style_names", "item_terms") for t in draft[key]]
            raws += [c["raw"] for key in ("include_categories", "exclude_categories") for c in draft[key]]
            raws += [draft["gender"]["raw"]] if draft["gender"] else []
            raws += [a["raw"] for a in draft["attributes"]] + [u["raw"] for u in draft["unsupported"]]
            for raw in raws:
                with self.subTest(id=item["id"], raw=raw):
                    self.assertIn(raw, item["query"])


@unittest.skipUnless(HAS_PYDANTIC, SKIP_REASON)
class DatasetAgainstCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy_v1 = importlib.import_module("vogue_orchestrator.query.legacy_v1")
        cls.assemble = importlib.import_module("vogue_orchestrator.query.assemble")
        cls.prompt = importlib.import_module("vogue_orchestrator.query.prompt")
        cls.dataset = load_dataset()

    def test_expected_drafts_are_valid_and_assemble_into_contract_valid_trend_queries(self):
        today = date.fromisoformat(self.dataset["reference_today"])
        for item in self.dataset["items"]:
            with self.subTest(id=item["id"]):
                draft = self.legacy_v1.QueryDraft.model_validate(item["expected_draft"])
                result = self.assemble.assemble_trend_query(
                    draft, request_id=item["id"], original_query=item["query"], today=today,
                )
                self.assertLessEqual(set(result.warnings), ALLOWED_WARNINGS)

    def test_dataset_does_not_reuse_any_prompt_example(self):
        # Reusing few-shot examples (of any version) would inflate any accuracy measured on this set.
        example_queries = {
            e["query"] for name in prompt_version_names() for e in self.prompt.load_prompt(name).examples
        }
        self.assertFalse(example_queries & {i["query"] for i in self.dataset["items"]})

    def test_dataset_prompt_version_folder_exists_and_has_the_v1_format(self):
        prefix, _, version = self.dataset["prompt_version"].partition("/")
        self.assertEqual(prefix, self.prompt.PROMPT_ID_PREFIX)
        self.assertIn(version, prompt_version_names())
        self.assertEqual(self.prompt.load_prompt(version).format, self.legacy_v1.DRAFT_FORMAT)


if __name__ == "__main__":
    unittest.main()
