import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fashion_planner_orchestrator.server import OrchestratorHandler
from fashion_planner_orchestrator.workflow_http import dispatch, routes


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        from _baseline_support import pin_proposed_baseline
        pin_proposed_baseline(self)

    def handler(self, body, path='/api/workflow/run'):
        handler = object.__new__(OrchestratorHandler)
        handler.path = path
        handler.command = 'POST'
        handler.client_address = ('127.0.0.1', 12345)
        handler.server = SimpleNamespace(server_port=8765)
        raw = json.dumps(body).encode()
        handler.headers = {'Content-Length': str(len(raw)), 'Host': '127.0.0.1:8765'}
        handler.rfile = io.BytesIO(raw)
        handler._json = Mock()
        return handler

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'workflow extra required')
    def test_live_gate_blocks_before_any_query_provider(self):
        request = {'contract_version': 'fashion-planner-workflow', 'request_id': 'test-gate',
                   'input': {'user_input': {'text': 'test', 'images': []}}}
        handler = self.handler(request)
        with patch.dict(os.environ), patch('fashion_planner_orchestrator.server.structure_query_for_ui') as structurer:
            for key in ('ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED', 'ORCHESTRATOR_WORKFLOW_CONTRACT_BASELINE'):
                os.environ.pop(key, None)
            dispatch(handler, request)
        self.assertEqual(handler._json.call_args.args[0], 409)
        self.assertEqual(handler._json.call_args.args[1]['error']['code'], 'CONTRACT_NOT_FROZEN')
        structurer.assert_not_called()

    def test_workflow_boundary_preserves_non_200_terminal_result(self):
        handler = self.handler({})
        response = {'http_status': 502, 'envelope': {'status': 'error', 'result': {'sources': {'trend': None}}}}
        fake = Mock()
        fake.run.return_value = response
        request = {'contract_version': 'fashion-planner-workflow', 'request_id': 'test',
                   'input': {'user_input': {'text': 'test', 'images': []}, 'trend_query': {'request_id': 'test'}}}
        with patch('fashion_planner_orchestrator.workflow_http.build_workflow', return_value=fake):
            dispatch(handler, request)
        handler._json.assert_called_once_with(502, response['envelope'])

    def test_requested_gender_reaches_the_planning_request(self):
        def planning_for(gender):
            trend = {'request_id': 'test-gender', 'original_query': 'test',
                     'trend_terms': {'season': {'year': 2026, 'season': 'FW', 'origin': 'user_requested'}},
                     'requested': {'categories': {'include': [{'major': '아우터', 'minor': '가디건'}]},
                                   'gender': gender}}
            request = {'contract_version': 'fashion-planner-workflow', 'request_id': 'test-gender',
                       'input': {'user_input': {'text': 'test', 'images': []}, 'trend_query': trend}}
            fake = Mock()
            fake.run.return_value = {'http_status': 200, 'envelope': {}}
            with patch('fashion_planner_orchestrator.workflow_http.build_workflow', return_value=fake):
                dispatch(self.handler(request), request)
            return fake.run.call_args.kwargs['planning_request']

        planning = planning_for({'raw': '여성', 'value': '여'})
        self.assertEqual(planning['context'], {'season': '2026FW', 'category': '가디건'})
        self.assertEqual(planning['requested_attributes'], [{'name': 'gender', 'value': 'women', 'source': 'user'}])
        self.assertEqual(planning_for({'raw': '공용', 'value': '공용'})['requested_attributes'][0]['value'], 'unisex')
        self.assertNotIn('requested_attributes', planning_for(None))

    def test_upload_limits_and_remote_boundary(self):
        handler = self.handler({}, '/v1/input-images')
        handler.headers = {'Content-Length': '10485761', 'Host': '127.0.0.1:8765'}
        self.assertTrue(routes(handler, handler.path))
        self.assertEqual(handler._json.call_args.args[0], 413)
        handler = self.handler({}, '/v1/input-images')
        handler.client_address = ('192.0.2.1', 100)
        routes(handler, handler.path)
        self.assertEqual(handler._json.call_args.args[0], 403)

    def test_service_manifest_is_deployment_configuration(self):
        from fashion_planner_orchestrator.service_manifest import DEFAULT_MANIFEST, services
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(DEFAULT_MANIFEST, root / 'deploy' / 'service-manifest.json')
        declared = json.loads(DEFAULT_MANIFEST.read_text(encoding='utf-8'))['services']
        missing = sorted({entry['repo'] for entry in declared.values() if not (root.parent / entry['repo']).is_dir()})
        if missing:
            self.skipTest('sibling checkouts not present: ' + ', '.join(missing))
        with patch.dict(os.environ):
            os.environ.pop('FASHION_PLANNER_SERVICE_MANIFEST', None)
            entries = services(require_runtime=False)
        self.assertEqual(next(entry for entry in entries if entry['name'] == 'stock')['port'], 8002)
        self.assertTrue(all('0.0.0.0' not in entry['args'] for entry in entries))
        self.assertEqual(next(entry for entry in entries if entry['name'] == 'sales-market')['repo'].name,
                         'research')
