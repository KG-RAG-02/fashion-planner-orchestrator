"""Guards that keep query structuring from colliding with the existing orchestrator. Stdlib only.

The module must stay standalone (portable to another repository), offline, and the only place
that uses pydantic. These checks read source files with `ast`; they import nothing.
"""

import ast
import unittest

from _query_support import QUERY_PACKAGE_DIR, SOURCE_DIR

CORE_MODULES = {"core", "connectors", "demo", "server"}
FORBIDDEN_IMPORT_ROOTS = {
    "urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp", "openai", "anthropic", "subprocess",
}


def _imports(path):
    """Yield (module, level) for every import statement in a source file."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


class IsolationTests(unittest.TestCase):
    def test_query_package_exists(self):
        self.assertTrue(any(QUERY_PACKAGE_DIR.glob("*.py")))

    def test_query_modules_do_not_import_the_orchestrator_core_or_parent_package(self):
        for path in QUERY_PACKAGE_DIR.glob("*.py"):
            for module, level in _imports(path):
                with self.subTest(file=path.name, module=module, level=level):
                    self.assertLessEqual(level, 1, "only same-package relative imports are allowed")
                    parts = module.split(".")
                    if level == 0 and parts[0] == "vogue_orchestrator":
                        # An absolute import may only point back into the query package itself.
                        self.assertEqual(parts[1:2], ["query"], f"must not import {module}")
                        self.assertNotIn(parts[1], CORE_MODULES)

    def test_query_modules_make_no_network_or_llm_calls(self):
        for path in QUERY_PACKAGE_DIR.glob("*.py"):
            for module, _level in _imports(path):
                with self.subTest(file=path.name, module=module):
                    self.assertNotIn(module.split(".")[0], FORBIDDEN_IMPORT_ROOTS)

    def test_existing_modules_do_not_import_the_query_package(self):
        for path in SOURCE_DIR.glob("*.py"):
            for module, level in _imports(path):
                with self.subTest(file=path.name, module=module):
                    parts = module.split(".")
                    self.assertFalse(
                        (level >= 1 and parts[0] == "query") or (level == 0 and parts[:2] == ["vogue_orchestrator", "query"]),
                        "the orchestrator core must not depend on the query module",
                    )

    def test_pydantic_is_only_used_inside_the_query_package(self):
        for path in SOURCE_DIR.glob("*.py"):
            for module, _level in _imports(path):
                with self.subTest(file=path.name, module=module):
                    self.assertNotEqual(module.split(".")[0], "pydantic")


if __name__ == "__main__":
    unittest.main()
