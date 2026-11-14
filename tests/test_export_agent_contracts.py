"""scripts/export_agent_contracts.py: constant reading, the workflow contract copy check, the registry files.

Offline and independent of the sibling checkouts: agent roots and contracts/workflow are replaced by temporary
directories wherever the script would read another repository. The MD decision schema is compared with the
live handler, which needs jsonschema (the `workflow` extra).
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'export_agent_contracts.py'

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - environment dependent
    Draft202012Validator = None


def load_script():
    name = 'export_agent_contracts_under_test'
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve the script's string annotations through sys.modules
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no scripts/__pycache__
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


eac = load_script()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class ReadConstantTests(unittest.TestCase):
    def read(self, text, name='VERSION'):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'module.py'
            path.write_text(text, encoding='utf-8')
            return eac.read_constant(path, name)

    def test_double_and_single_quoted_constants(self):
        self.assertEqual(self.read('CONTRACT_VERSION = "vogue-handoff/0.1"\n', 'CONTRACT_VERSION'), 'vogue-handoff/0.1')
        self.assertEqual(self.read("VERSION = 'vogue-workflow/0.1'\n"), 'vogue-workflow/0.1')

    def test_annotated_constant(self):
        self.assertEqual(self.read("VERSION: Final[str] = 'x/1'  # pinned\n"), 'x/1')

    def test_only_the_first_module_level_literal_of_that_exact_name(self):
        text = "VERSION_X = 'a'\nXVERSION = 'b'\nclass C:\n    VERSION = 'nested'\nVERSION = 'top'\nVERSION = 'later'\n"
        self.assertEqual(self.read(text), 'top')
        for text in ('VERSION = OTHER\n', 'VERSION = \'mixed"\n', 'VERSION = ""\n', 'VERSION = """doc"""\n',
                     "    VERSION = 'indented'\n", "VERSION == 'x'\n"):
            with self.subTest(text=text):
                self.assertIsNone(self.read(text))

    def test_missing_file(self):
        self.assertIsNone(eac.read_constant(ROOT / 'no-such-module.py', 'VERSION'))

    def test_agent_route_with_a_single_quoted_constant(self):
        envelope = eac.Envelope('/v1/agents/sales-market-agent/reviews', 'src/reviews.py', 'VERSION',
                                'src/vogue_orchestrator/workflow_contract.py:VERSION')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'src' / 'reviews.py'
            source.parent.mkdir()
            source.write_text("VERSION = 'vogue-workflow/0.1'\n", encoding='utf-8')
            with patch.dict(os.environ, {'VOGUE_SALES_MARKET_ROOT': tmp}):
                found = eac.envelope_version('sales-market-agent', envelope)
        self.assertEqual(found, {'route': '/v1/agents/sales-market-agent/reviews',
                                 'contract_version': 'vogue-workflow/0.1', 'detected_from': 'src/reviews.py:VERSION'})


class OrchestratorValueTests(unittest.TestCase):
    """The orchestrator side of each comparison reads the real constants of this repository."""

    def test_runs_routes_use_the_current_workflow_handoff_version(self):
        from vogue_orchestrator.workflow_contract import HANDOFF_VERSION
        self.assertEqual(eac.orchestrator_contract_version(), HANDOFF_VERSION)
        self.assertEqual(eac.orchestrator_contract_version('trend-analysis-agent'), HANDOFF_VERSION)
        self.assertEqual(eac.orchestrator_contract_version('new-product-agent'), HANDOFF_VERSION)
        self.assertEqual(eac.expected_from('stock-agent', eac.ENVELOPES['stock-agent']),
                         'src/vogue_orchestrator/workflow_contract.py:HANDOFF_VERSION')

    def test_an_envelope_names_the_file_and_constant_it_is_compared_with(self):
        from vogue_orchestrator.workflow_contract import VERSION  # single-quoted in the source
        for agent_id, envelope in eac.WORKFLOW_ENVELOPES.items():
            with self.subTest(agent_id=agent_id):
                expected = ('src/vogue_orchestrator/workflow_contract.py:P07_REVIEW_VERSION'
                            if agent_id == 'sales-market-agent' else
                            'src/vogue_orchestrator/workflow_contract.py:P06_REVIEW_BRIDGE_VERSION'
                            if agent_id == 'new-product-agent-review-bridge' else
                            'src/vogue_orchestrator/workflow_contract.py:VERSION')
                self.assertEqual(eac.expected_from(agent_id, envelope), expected)
                self.assertEqual(eac.orchestrator_contract_version(agent_id, envelope),
                                 'vogue-sales-market-review/0.2-proposal' if agent_id == 'sales-market-agent' else
                                 'vogue-sales-market-review/0.1-proposal' if agent_id == 'new-product-agent-review-bridge' else VERSION)
        self.assertEqual(eac.envelope_version(eac.SELF, eac.RUN_ENVELOPE)['contract_version'], VERSION)


class WorkflowCopyCheckTests(unittest.TestCase):
    FILES = {'common.schema.json': b'{\n  "a": 1\n}\n', 'upstream/trend-result-1.1.schema.json': b'{"t": "1.1"}\n'}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.workflow = self.tmp / 'p08' / 'contracts' / 'workflow'
        self.write_tree(self.workflow, self.FILES)
        pins = {rel: sha256(data) for rel, data in self.FILES.items()}
        (self.workflow / 'snapshot.json').write_text(json.dumps({'files': pins}), encoding='utf-8')
        self.roots = {}
        for patcher in (patch.object(eac, 'WORKFLOW', self.workflow),
                        patch.dict(os.environ, {eac.REPOS[a][0]: str(root) for a, root in self.roots.items()})):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def write_tree(base, files):
        for rel, data in files.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def run_main(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = eac.main(list(argv))
        return code, buffer.getvalue()

    def test_identical_copies_pass(self):
        lines, problems = eac.vendored_check()
        self.assertEqual(problems, [])
        self.assertEqual(lines, ['OK     vogue-orchestrator/contracts/workflow (2 files)'])

    def test_a_crlf_checkout_still_matches(self):
        self.assertEqual(eac.vendored_check()[1], [])

    def test_changed_or_missing_copies_are_reported(self):
        (self.workflow / 'upstream/trend-result-1.1.schema.json').write_bytes(b'{"t": "1.2"}\n')
        lines, problems = eac.vendored_check()
        self.assertEqual(problems, ['vogue-orchestrator/contracts/workflow/upstream/trend-result-1.1.schema.json '
                                    '(sha256 differs from snapshot.json)'])
        self.assertEqual([line[:6] for line in lines], ['STALE '])

    def test_a_canonical_file_that_left_its_pin_is_reported(self):
        (self.workflow / 'common.schema.json').write_bytes(b'{"a": 2}\n')
        self.assertEqual(eac.vendored_check()[1],
                         ['vogue-orchestrator/contracts/workflow/common.schema.json (sha256 differs from snapshot.json)'])

    def test_an_absent_checkout_is_skipped_with_the_reason(self):
        absent = self.tmp / 'absent'
        with patch.dict(os.environ, {'VOGUE_NEW_PRODUCT_ROOT': str(absent)}):
            lines, problems = eac.vendored_check()
        self.assertEqual(problems, [])
        self.assertEqual(lines, ['OK     vogue-orchestrator/contracts/workflow (2 files)'])

    def test_only_check_mode_fails_on_a_differing_copy(self):
        agents = self.tmp / 'agents'
        agents.mkdir()
        # No registry entries and no route comparisons: main() runs the copy check against the temporary trees.
        with patch.multiple(eac, ENTRIES=(), ENVELOPES={}, WORKFLOW_ENVELOPES={}, CONTRACTS=agents):
            (agents / 'SOURCES.md').write_text(eac.render_sources(), encoding='utf-8', newline='\n')
            self.assertEqual(self.run_main('--check')[0], 0)
            changed = self.workflow / 'common.schema.json'
            changed.write_bytes(b'{"a": 3}\n')
            code, out = self.run_main('--check')
            self.assertEqual(code, 1)
            self.assertIn('STALE  vogue-orchestrator/contracts/workflow/common.schema.json', out)
            self.assertIn('1 workflow contract file(s) differ from contracts/workflow/snapshot.json', out)
            code, _ = self.run_main()
        self.assertEqual((code, changed.read_bytes()), (0, b'{"a": 3}\n'))  # never rewrites a vendored copy


class RegistryFileTests(unittest.TestCase):
    def load(self, entry):
        return json.loads((eac.CONTRACTS / entry.agent_id / entry.direction / entry.target).read_text(encoding='utf-8'))

    def test_every_entry_has_its_registry_file(self):
        for entry in eac.ENTRIES:
            with self.subTest(target=entry.target):
                value = self.load(entry)
                self.assertIn('json_schema', value)
                if entry.method == 'pinned':
                    continue  # trend-query keeps its older provenance format
                provenance = value['_provenance']
                self.assertEqual((provenance['agent_id'], provenance['direction'], provenance['method']),
                                 (entry.agent_id, entry.direction, entry.method))
                self.assertEqual([item['path'] for item in provenance['source_files']], list(entry.source_files))
        self.assertFalse((eac.CONTRACTS / 'new-product-agent' / 'input' / 'planning-draft-request-1.0.contract.json')
                         .exists())
        self.assertFalse((eac.CONTRACTS / 'sales-market-agent' / 'input' / 'sales-market-review-request-1.0.contract.json')
                         .exists())
        self.assertFalse((eac.CONTRACTS / 'vogue-orchestrator' / 'output' / 'integrated-result-1.0.contract.json')
                         .exists())

    def test_workflow_copies_equal_the_pinned_schemas(self):
        pins = json.loads((eac.WORKFLOW / 'snapshot.json').read_text(encoding='utf-8'))['files']
        copies = [entry for entry in eac.ENTRIES if entry.method == 'copy' and entry.agent_id == 'vogue-orchestrator']
        self.assertEqual({Path(entry.source).name for entry in copies}, {'integrated-result.schema.json'})
        self.assertFalse((eac.CONTRACTS / 'new-product-agent' / 'input' / 'planning-draft-request-1.0.contract.json')
                         .exists())
        self.assertFalse((eac.CONTRACTS / 'sales-market-agent' / 'input' / 'sales-market-review-request-1.0.contract.json')
                         .exists())
        self.assertFalse((eac.CONTRACTS / 'vogue-orchestrator' / 'output' / 'integrated-result-1.0.contract.json')
                         .exists())
        for entry in copies:
            with self.subTest(target=entry.target):
                value = self.load(entry)
                canonical = eac.WORKFLOW / Path(entry.source).name
                schema = json.loads(canonical.read_text(encoding='utf-8'))
                self.assertEqual(value['json_schema'], schema)
                for item in value['_provenance']['source_files']:
                    if item['path'] in pins:
                        self.assertEqual(item['sha256'], pins[item['path']], item['path'])
                expected = ('vogue-workflow/0.2' if entry.agent_id == 'vogue-orchestrator' else
                            'vogue-sales-market-review/0.1-proposal' if entry.target == 'sales-market-review-bridge-0.1.contract.json' else
                            'vogue-sales-market-review/0.2-proposal')
                self.assertEqual(value['_provenance']['handoff']['contract_version'], expected)


@unittest.skipUnless(Draft202012Validator, 'jsonschema not installed (install the `workflow` extra)')
class MdDecisionContractTests(unittest.TestCase):
    """The registry schema accepts exactly the bodies POST /api/workflow/runs/{rid}/md-decision accepts."""

    SHA = '0123456789abcdef' * 4
    BASE = {'contract_version': 'vogue-workflow/0.2', 'request_id': 'run-1', 'integrated_response_sha256': SHA,
            'decision': 'adopt'}

    @classmethod
    def setUpClass(cls):
        from vogue_orchestrator import workflow_http
        cls.http = workflow_http
        path = eac.CONTRACTS / 'vogue-orchestrator' / 'input' / 'md-decision-request-2.0.contract.json'
        schema = json.loads(path.read_text(encoding='utf-8'))['json_schema']
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def body(self, **changes):
        value = {**self.BASE, **changes}
        return {key: item for key, item in value.items() if item is not ...}

    def test_schema_and_handler_agree(self):
        body, sha = self.body, self.SHA
        cases = [body(), body(decision='revise'), body(decision='hold', note=None), body(note=''), body(note='x' * 2000),
                 body(note='한' * 2000), body(request_id='A.b_c:d-1'), body(request_id='r' * 128),
                 body(note='x' * 2001), body(note=7), body(note=False), body(note=['x']), body(extra=True),
                 body(contract_version='vogue-workflow/0.2'), body(contract_version=...), body(request_id=...),
                 body(request_id=''), body(request_id='-run'), body(request_id='r' * 129), body(request_id='run 1'),
                 body(request_id=7), body(integrated_response_sha256=sha.upper()),
                 body(integrated_response_sha256=sha[:-1]), body(integrated_response_sha256=...),
                 body(integrated_response_sha256=None), body(decision='approve'), body(decision=...),
                 body(decision=['adopt']), body(decision=None), body(decision=True), ['adopt'], 'adopt', None]
        # A trailing newline is left out: Python's re.search lets `$` match before it, ECMA-262 does not.
        for value in cases:
            rid = value.get('request_id') if isinstance(value, dict) else None
            if not (isinstance(rid, str) and self.http.ID_PATTERN.fullmatch(rid)):
                rid = 'run-1'  # an invalid path id answers 404 before the body is read
            with self.subTest(body=value):
                self.assertEqual(self.validator.is_valid(value), self.http._md_decision_error(value, rid) is None)

    def test_request_id_must_also_equal_the_path_id(self):
        self.assertTrue(self.validator.is_valid(self.body()))
        self.assertEqual(self.http._md_decision_error(self.body(), 'run-2'), 'REQUEST_ID_MISMATCH')


if __name__ == '__main__':
    unittest.main()
