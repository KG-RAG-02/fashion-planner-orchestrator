"""In-repo contract gate (conventions section 9): frozen runs, development override, 409 otherwise."""
import copy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from vogue_orchestrator.workflow_contract import (CONTRACT_ROOT, baseline_problems, contract_gate_status,
                                                  snapshot_state)

ROOT = Path(__file__).resolve().parents[1]
DEV_LIMITATION = 'workflow-contract/0.2.0 not frozen; development run'
GATE_KEYS = ('VOGUE_WORKFLOW_ALLOW_PROPOSED', 'VOGUE_WORKFLOW_CONTRACT_BASELINE')


def _freeze_script():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        return importlib.import_module('freeze_workflow_contract')
    finally:
        sys.path.remove(str(ROOT / 'scripts'))


def frozen(baseline):
    value = copy.deepcopy(baseline)
    value.update(state='frozen', producer_consumer_validated=True,
                 evidence={'reference_fixtures': {'cases': 66, 'passed': 66, 'failed': 0, 'failed_cases': []},
                           'mutation_probes': {'cases': 3, 'rejected': 3, 'accepted': []},
                           'suites': [{'command': 'synthetic', 'returncode': 0}]})
    return value


class ContractGateTests(unittest.TestCase):
    def setUp(self):
        self.baseline = json.loads((CONTRACT_ROOT / 'baseline.json').read_text(encoding='utf-8'))
        from _baseline_support import pin_proposed_baseline
        self.baseline = pin_proposed_baseline(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write(self, value, name='baseline.json'):
        path = self.tmp / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def test_in_repo_proposed_baseline_is_consistent_but_closed(self):
        status = contract_gate_status({})
        self.assertEqual(status['state'], 'proposed')
        self.assertEqual(status['source'], 'contracts/workflow/baseline.json')
        self.assertTrue(status['consistent'], status['problems'])
        self.assertFalse(status['frozen'])
        self.assertEqual(status['gate'], 'closed')
        self.assertEqual(status['contract_version'], 'workflow-contract/0.2.0')

    def test_allow_proposed_opens_a_development_run_with_the_exact_limitation(self):
        from vogue_orchestrator.workflow_http import ensure_live_contract
        for value in ('1', 'true', 'YES', 'on'):
            with self.subTest(value=value):
                self.assertEqual(contract_gate_status({'VOGUE_WORKFLOW_ALLOW_PROPOSED': value})['gate'], 'development')
                self.assertEqual(ensure_live_contract(env={'VOGUE_WORKFLOW_ALLOW_PROPOSED': value}), [DEV_LIMITATION])
        for value in ('', '0', 'false', 'no'):
            with self.subTest(value=value):
                self.assertEqual(contract_gate_status({'VOGUE_WORKFLOW_ALLOW_PROPOSED': value})['gate'], 'closed')

    def test_closed_gate_raises_contract_not_frozen_409(self):
        from vogue_orchestrator.workflow_http import WorkflowUnavailable, ensure_live_contract
        with self.assertRaises(WorkflowUnavailable) as raised:
            ensure_live_contract(env={})
        self.assertEqual((raised.exception.code, raised.exception.http_status), ('CONTRACT_NOT_FROZEN', 409))

    def test_frozen_validated_matching_baseline_runs_without_limitation(self):
        from vogue_orchestrator.workflow_http import ensure_live_contract
        env = {'VOGUE_WORKFLOW_CONTRACT_BASELINE': self.write(frozen(self.baseline))}
        status = contract_gate_status(env)
        self.assertEqual((status['gate'], status['source'], status['problems']),
                         ('open', 'VOGUE_WORKFLOW_CONTRACT_BASELINE', []))
        self.assertEqual(ensure_live_contract(env=env), [])

    def test_frozen_baseline_drift_or_failures_close_the_gate(self):
        good = frozen(self.baseline)
        cases = {
            'snapshot_sha256': dict(good, snapshot_sha256='0' * 64),
            'files': dict(good, files={k: v for k, v in list(good['files'].items())[1:]}),
            'frozen_without_validation': dict(good, producer_consumer_validated=False),
            'frozen_with_reference_failures': dict(good, evidence=dict(good['evidence'], reference_fixtures={'failed': 2})),
            'frozen_with_suite_failures': dict(good, evidence=dict(good['evidence'], suites=[{'returncode': 1}])),
            'frozen_with_accepted_mutations': dict(good, evidence=dict(good['evidence'], mutation_probes={'accepted': ['x']})),
            'format': dict(good, format='other/1'),
        }
        for problem, value in cases.items():
            with self.subTest(problem=problem):
                status = contract_gate_status({'VOGUE_WORKFLOW_CONTRACT_BASELINE': self.write(value, problem + '.json')})
                self.assertIn(problem, status['problems'])
                self.assertEqual(status['gate'], 'closed')
                self.assertEqual(contract_gate_status({'VOGUE_WORKFLOW_CONTRACT_BASELINE': str(self.tmp / (problem + '.json')),
                                                       'VOGUE_WORKFLOW_ALLOW_PROPOSED': '1'})['gate'], 'development')

    def test_missing_or_invalid_override_fails_closed(self):
        missing = contract_gate_status({'VOGUE_WORKFLOW_CONTRACT_BASELINE': str(self.tmp / 'absent.json')})
        self.assertEqual((missing['state'], missing['gate']), ('missing', 'closed'))
        broken = self.tmp / 'broken.json'
        broken.write_bytes(b'{"state":"frozen","state":"frozen"}')
        invalid = contract_gate_status({'VOGUE_WORKFLOW_CONTRACT_BASELINE': str(broken)})
        self.assertEqual((invalid['state'], invalid['gate']), ('invalid', 'closed'))

    def test_problems_are_at_least_as_strict_as_the_freeze_script(self):
        script = _freeze_script()
        snapshot, snapshot_sha, file_errors = snapshot_state()
        self.assertEqual((snapshot, snapshot_sha, file_errors), script.snapshot_state())
        good = frozen(self.baseline)
        variants = [self.baseline, good, dict(good, format='x'), dict(good, snapshot_sha256='1' * 64),
                    dict(good, files={}), dict(good, contract_version='workflow-contract/9'),
                    dict(good, producer_consumer_validated=False), dict(good, evidence={}),
                    dict(good, evidence=dict(good['evidence'], suites=[{'returncode': 2}])),
                    dict(good, evidence=dict(good['evidence'], mutation_probes={'accepted': ['m']}))]
        for index, value in enumerate(variants):
            with self.subTest(variant=index):
                ours = baseline_problems(value, snapshot, snapshot_sha, file_errors)
                theirs = script.check_baseline(value, snapshot, snapshot_sha, file_errors)
                self.assertLessEqual(set(theirs), set(ours))
                if index < 2:
                    self.assertEqual((ours, theirs), ([], []))

    def test_readiness_reports_the_in_repo_baseline_state(self):
        from vogue_orchestrator.workflow_http import readiness
        with patch.dict(os.environ):
            for key in GATE_KEYS:
                os.environ.pop(key, None)
            report = readiness()
        self.assertEqual((report['baseline_state'], report['contract_gate']), ('proposed', 'closed'))

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'workflow extra (jsonschema) required')
    def test_build_workflow_carries_the_development_limitation(self):
        from vogue_orchestrator import workflow_http
        from vogue_orchestrator.workflow_store import WorkflowStore
        env = {'VOGUE_WORKFLOW_ALLOW_PROPOSED': '1', 'VOGUE_AGENT_HTTP_DEV_MODE': '1',
               'VOGUE_TREND_AGENT_URL': 'http://127.0.0.1:9', 'VOGUE_STOCK_AGENT_URL': 'http://127.0.0.1:9',
               'VOGUE_NEW_PRODUCT_AGENT_URL': 'http://127.0.0.1:9', 'VOGUE_SALES_MARKET_AGENT_URL': 'http://127.0.0.1:9'}
        store = WorkflowStore(Path(os.path.realpath(self.tmp)) / 'store')
        with patch.dict(os.environ, env), patch.object(workflow_http, 'store', return_value=store):
            os.environ.pop('VOGUE_WORKFLOW_CONTRACT_BASELINE', None)
            workflow = workflow_http.build_workflow()
        self.assertEqual(workflow.limitations, (DEV_LIMITATION,))


if __name__ == '__main__':
    unittest.main()
