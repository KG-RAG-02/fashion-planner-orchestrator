"""Guards that keep query structuring independent from transport and provider code. Stdlib only.

The module must stay standalone (portable to another repository), offline, and the only place
that uses pydantic. These checks read source files with `ast`; they import nothing.
"""

import ast
import unittest

from _query_support import QUERY_PACKAGE_DIR, SOURCE_DIR

QUERY_CALLERS = {"server.py"}  # query package is loaded lazily by the current UI request path
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

    def test_query_modules_do_not_import_transport_or_parent_package(self):
        for path in QUERY_PACKAGE_DIR.glob("*.py"):
            for module, level in _imports(path):
                with self.subTest(file=path.name, module=module, level=level):
                    self.assertLessEqual(level, 1, "only same-package relative imports are allowed")
                    parts = module.split(".")
                    if level == 0 and parts[0] == "fashion_planner_orchestrator":
                        # An absolute import may only point back into the query package itself.
                        self.assertEqual(parts[1:2], ["query"], f"must not import {module}")

    def test_query_modules_make_no_network_or_llm_calls(self):
        for path in QUERY_PACKAGE_DIR.glob("*.py"):
            for module, _level in _imports(path):
                with self.subTest(file=path.name, module=module):
                    self.assertNotIn(module.split(".")[0], FORBIDDEN_IMPORT_ROOTS)

    def test_existing_modules_do_not_import_the_query_package(self):
        # server.py is the only runtime module that invokes query structuring.
        for path in SOURCE_DIR.glob("*.py"):
            if path.name in QUERY_CALLERS:
                continue
            for module, level in _imports(path):
                with self.subTest(file=path.name, module=module):
                    parts = module.split(".")
                    self.assertFalse(
                        (level >= 1 and parts[0] == "query") or (level == 0 and parts[:2] == ["fashion_planner_orchestrator", "query"]),
                        "only the HTTP entry point may depend on query structuring",
                    )

    def test_pydantic_is_only_used_inside_the_query_package(self):
        for path in SOURCE_DIR.glob("*.py"):
            for module, _level in _imports(path):
                with self.subTest(file=path.name, module=module):
                    self.assertNotEqual(module.split(".")[0], "pydantic")



if __name__ == "__main__":
    unittest.main()
