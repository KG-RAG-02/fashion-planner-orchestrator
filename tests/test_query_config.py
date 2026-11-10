"""configs/query_structuring.json: loading, validation, and that the code really uses it. Stdlib only."""

import json
import tempfile
import unittest
from pathlib import Path

from _query_support import ROOT
from vogue_orchestrator.query import config as query_config
from vogue_orchestrator.query.vocabulary import GENDERS

CONFIG_FILE = ROOT / "configs" / "query_structuring.json"


class QueryConfigTests(unittest.TestCase):
    def test_repository_config_loads_with_expected_values(self):
        config = query_config.load_config(CONFIG_FILE)
        self.assertEqual((config.prompt_name, config.prompt_version), ("query_structuring", "v1"))
        self.assertEqual(config.max_query_chars, 1000)
        self.assertEqual((config.draft_year_min, config.draft_year_max), (2000, 2100))
        self.assertEqual(set(config.unsupported_reasons), {"brand", "category", "attribute", "abstract"})

    def test_gender_table_maps_only_to_standard_genders(self):
        config = query_config.load_config(CONFIG_FILE)
        self.assertEqual(set(config.gender_aliases), set(GENDERS))
        self.assertIn("여성", config.gender_aliases["여"])
        self.assertIn("남성", config.gender_aliases["남"])
        self.assertIn("유니섹스", config.gender_aliases["공용"])

    def test_active_config_is_the_repository_file_by_default(self):
        self.assertEqual(query_config.CONFIG.source.resolve(), CONFIG_FILE.resolve())

    def test_prompt_folder_of_the_configured_version_exists(self):
        config = query_config.CONFIG
        self.assertTrue(query_config.prompt_version_dir(config.prompt_name, config.prompt_version).is_dir())

    def test_broken_or_inconsistent_files_fail_loudly(self):
        good = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        broken_cases = {
            "not json": "{",
            "missing section": json.dumps({k: v for k, v in good.items() if k != "draft"}),
            "wrong type": json.dumps({**good, "input": {"max_query_chars": "1000"}}),
            "inverted bounds": json.dumps({**good, "draft": {"year_min": 2100, "year_max": 2000}}),
            "missing gender table": json.dumps({k: v for k, v in good.items() if k != "gender"}),
            "gender value not standard": json.dumps({**good, "gender": {"aliases": {"여성": ["여자"]}}}),
            "gender spellings not a list": json.dumps({**good, "gender": {"aliases": {"여": "여성"}}}),
            "gender spelling maps to two values": json.dumps(
                {**good, "gender": {"aliases": {"여": ["여성"], "남": ["여 성"]}}}),
            "gender spelling is another standard value": json.dumps({**good, "gender": {"aliases": {"여": ["남"]}}}),
            "gender spelling empty after normalization": json.dumps({**good, "gender": {"aliases": {"여": ["-"]}}}),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, text in broken_cases.items():
                with self.subTest(case=name):
                    path = Path(directory) / "config.json"
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(query_config.QueryConfigError):
                        query_config.load_config(path)
            with self.assertRaises(query_config.QueryConfigError):
                query_config.load_config(Path(directory) / "missing.json")


class ConfigIsUsedByCodeTests(unittest.TestCase):
    """The values must come from the config, not from leftover literals in the code."""

    def test_no_moved_literal_remains_in_the_query_package(self):
        package = ROOT / "src" / "vogue_orchestrator" / "query"
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        moved_texts = [*config["unsupported"]["reasons"].values(), config["unsupported"]["generic_reason"]]
        for path in package.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for text in moved_texts:
                with self.subTest(file=path.name, text=text[:10]):
                    self.assertNotIn(text.split("{")[0], source)
            with self.subTest(file=path.name, check="no prompt sentences in code"):
                self.assertNotIn("당신은 한국어 패션 기획 질의", source)


if __name__ == "__main__":
    unittest.main()
