"""The repository carries no personal paths and no reference to private coordination folders.

Every tracked text file is scanned (or, without git, every file under the repository root). The tokens are
private by nature: a home directory path, the coordination folders used while the five repositories were
prepared, and the old evidence archive folder.
"""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    # the directory names as path tokens; identifiers such as build_workflow do not match
    'coordination folder _workflow': re.compile(r'(?<![A-Za-z0-9_])_workflow(?![A-Za-z0-9_])'),
    'coordination folder _oss-release': re.compile(r'(?<![A-Za-z0-9_])_oss-release(?![A-Za-z0-9_])'),
    'home directory path': re.compile(r'/Users/[A-Za-z0-9._-]+'),
    'old evidence archive': re.compile(r'VOGUE-archive'),
    'personal notes folder': re.compile(r'Desktop/Knowledge Graph'),
}
SKIP_DIRS = {'.git', '.local', '__pycache__', 'node_modules', 'build', 'dist'}
ICLOUD_COPY = re.compile(r' \d+(\.[^.]+)?$')


def tracked_files():
    try:
        listed = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout
        return [ROOT / name for name in listed.decode('utf-8').split('\0') if name]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob('*') if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def hits_in(path):
    try:
        text = path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return []  # binary files (fonts, images, PNG fixtures) carry no paths
    return [f'{path.relative_to(ROOT)}:{n} ({label})' for n, line in enumerate(text.splitlines(), 1)
            for label, pattern in FORBIDDEN.items() if pattern.search(line)]


class RepositoryHygieneTests(unittest.TestCase):
    def test_no_personal_paths_or_private_folders(self):
        this = Path(__file__).resolve()
        hits = []
        for path in tracked_files():
            if path.resolve() == this or ICLOUD_COPY.search(path.name) or not path.is_file():
                continue
            hits += hits_in(path)
        self.assertEqual(hits, [])

    def test_patterns_match_paths_but_not_identifiers(self):
        directory = FORBIDDEN['coordination folder _workflow']
        self.assertTrue(directory.search("ROOT.parent / '_workflow/decisions'"))
        self.assertTrue(directory.search('../_workflow'))
        self.assertFalse(directory.search('build_workflow(); freeze_workflow_contract'))
        self.assertTrue(FORBIDDEN['home directory path'].search('/Users/someone/project'))
        self.assertFalse(FORBIDDEN['home directory path'].search('src/users/list.py'))


if __name__ == '__main__':
    unittest.main()
