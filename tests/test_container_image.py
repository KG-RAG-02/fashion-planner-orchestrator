"""Static guards for the image: every COPY source is in the build context, nothing private is.

The context rules follow Docker's .dockerignore semantics (last matching rule wins; a rule that
matches a parent directory applies to everything below it; ** spans directories). The real build
is verified with `docker build`; this test catches the class of bug where the Dockerfile copies a
directory the ignore file drops.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _regex(pattern):
    """Mirror moby/patternmatcher: '**' at the end is '.*'; '**' or '**/' elsewhere is '(.*/)?'."""
    out, i = '', 0
    while i < len(pattern):
        if pattern.startswith('**', i):
            i += 2
            if pattern.startswith('/', i):
                i += 1
            out += '.*' if i >= len(pattern) else '(?:.*/)?'
        elif pattern[i] == '*':
            out, i = out + '[^/]*', i + 1
        elif pattern[i] == '?':
            out, i = out + '[^/]', i + 1
        elif pattern[i] == '[':
            end = pattern.index(']', i)
            out, i = out + pattern[i:end + 1], end + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r'\Z')


def load_rules():
    rules = []
    for line in (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        negated = line.startswith('!')
        rules.append((negated, _regex(line.lstrip('!').strip('/'))))
    return rules


def excluded(rules, path):
    candidates = [path] + ['/'.join(path.split('/')[:n]) for n in range(1, path.count('/') + 1)]
    verdict = False
    for negated, regex in rules:
        if any(regex.match(candidate) for candidate in candidates):
            verdict = not negated
    return verdict


class ContainerImageTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()
        self.dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')

    def test_every_copy_source_is_in_the_build_context(self):
        sources = [line.split()[1] for line in self.dockerfile.splitlines() if line.startswith('COPY ')]
        self.assertIn('examples/fixtures/query', sources)  # ORCHESTRATOR_QUERY_STRUCTURING_MODE=synthetic_fixture
        for source in sources:
            files = [source] if (ROOT / source).is_file() else [str(p.relative_to(ROOT)) for p in (ROOT / source).rglob('*')
                                                                if p.is_file() and '__pycache__' not in p.parts]
            self.assertTrue(files, source)
            for path in files:
                if re.search(r' \d+(\.[^.]+)?$', path):
                    continue  # iCloud conflict copies are dropped on purpose
                with self.subTest(path=path):
                    self.assertFalse(excluded(self.rules, path))

    def test_private_and_repository_only_material_stays_out(self):
        for path in ('.git/HEAD', '.env', '.envrc', '.env.local', 'configs/.env', '.local/latest-run.json',
                     '.local/workflow/blobs/x', 'tests/test_workflow.py', 'tools/workflow_contract/support.py',
                     'deploy/service-manifest.json', 'docs/workflow-integration-conventions.md', 'data/query-structuring-eval/dev.json',
                     'scripts/local_servers.py', 'compose.yaml', 'README.md',
                     'src/fashion_planner_orchestrator/server 2.py', 'web/app 2.js', 'contracts/workflow/baseline 3.json',
                     'prompts/query_structuring/examples 2.json',
                     'src/fashion_planner_orchestrator/__pycache__/server.cpython-312.pyc'):
            with self.subTest(path=path):
                self.assertTrue(excluded(self.rules, path))

    def test_runtime_user_store_and_healthcheck(self):
        for fragment in ('USER 10001:10001', 'PYTHONPATH=/app/src', 'HOME=/tmp', 'ORCHESTRATOR_RUNTIME=container',
                         'mkdir -p /app/.local/workflow/blobs', 'chown -R 10001:10001 /app/.local',
                         'ORCHESTRATOR_WORKFLOW_STORE_DIR=/app/.local/workflow'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.dockerfile)
        healthcheck = next(line for line in self.dockerfile.splitlines() if 'urlopen' in line)
        self.assertIn('/api/health', healthcheck)
        self.assertLess(self.dockerfile.index('HEALTHCHECK'), self.dockerfile.index('CMD ["python", "app.py"]'))


if __name__ == '__main__':
    unittest.main()
