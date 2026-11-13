"""The runtime, the launcher and the tests never depend on the external coordination directory."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
# The directory name as a path token; identifiers such as build_workflow do not match.
EXTERNAL_DIRECTORY = re.compile(r'(?<![A-Za-z0-9_])_workflow(?![A-Za-z0-9_])')
ICLOUD_COPY = re.compile(r' \d+(\.[^.]+)?$')


class RepositoryHygieneTests(unittest.TestCase):
    def test_no_reference_to_the_external_workflow_directory(self):
        this = Path(__file__).resolve()
        targets = [ROOT / 'src', ROOT / 'tests', ROOT / 'scripts' / 'local_servers.py']
        hits = []
        for target in targets:
            paths = [target] if target.is_file() else sorted(p for p in target.rglob('*') if p.is_file())
            for path in paths:
                if path.resolve() == this or '__pycache__' in path.parts or ICLOUD_COPY.search(path.name):
                    continue
                try:
                    text = path.read_text(encoding='utf-8')
                except (UnicodeDecodeError, OSError):
                    continue  # binary fixtures (PNG) carry no paths
                hits += [f'{path.relative_to(ROOT)}:{n}' for n, line in enumerate(text.splitlines(), 1)
                         if EXTERNAL_DIRECTORY.search(line)]
        self.assertEqual(hits, [])

    def test_pattern_matches_paths_but_not_identifiers(self):
        self.assertTrue(EXTERNAL_DIRECTORY.search("ROOT.parent / '_workflow/decisions'"))
        self.assertTrue(EXTERNAL_DIRECTORY.search('../_workflow'))
        self.assertFalse(EXTERNAL_DIRECTORY.search('build_workflow(); freeze_workflow_contract'))


if __name__ == '__main__':
    unittest.main()
