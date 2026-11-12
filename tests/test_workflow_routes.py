"""Explicit workflow route table: documented methods/paths only; nothing else starts a run."""
import importlib.util
import unittest
from unittest.mock import patch

from _http_support import LiveServerMixin, environment
from vogue_orchestrator import workflow_http
from vogue_orchestrator.workflow_store import WorkflowStore


class WorkflowRouteTableTests(LiveServerMixin, unittest.TestCase):
    def setUp(self):
        self.start_server()
        self.store = WorkflowStore(self.state / 'workflow')
        for target, value in (('store', lambda: self.store),):
            patcher = patch.object(workflow_http, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.real_dispatch = workflow_http.dispatch
        dispatch = patch.object(workflow_http, 'dispatch', side_effect=AssertionError('must not start a run'))
        dispatch.start()
        self.addCleanup(dispatch.stop)

    def code(self, *args, **kwargs):
        status, body, response = self.request(*args, **kwargs)
        return status, body['error']['code'], response

    def test_unknown_workflow_paths_are_404_problems(self):
        with environment():
            for method, path in (('GET', '/api/workflow'), ('GET', '/api/workflow/'), ('GET', '/api/workflow/unknown'),
                                 ('GET', '/api/workflow/runs/'), ('POST', '/api/workflow/runs/x/extra'),
                                 ('POST', '/api/workflow/runs/x/md-decisions'), ('POST', '/api/workflow/artifacts/'),
                                 ('POST', '/api/workflow/start'), ('DELETE', '/api/workflow/anything')):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.code(method, path, {} if method == 'POST' else None)[:2], (404, 'ROUTE_NOT_FOUND'))

    def test_documented_paths_with_other_methods_are_405_problems(self):
        with environment():
            for method, path, allow in (('POST', '/api/workflow/runs/run-1', 'GET'),
                                        ('POST', '/api/workflow/artifacts/' + 'a' * 64, 'GET'),
                                        ('GET', '/api/workflow/run', 'POST'), ('DELETE', '/api/workflow/runs/run-1', 'GET'),
                                        ('PUT', '/api/workflow/run', 'POST'), ('PATCH', '/api/workflow/run', 'POST'),
                                        ('GET', '/v1/input-images', 'POST'), ('GET', '/v1/orchestrations/image-retries', 'POST')):
                with self.subTest(method=method, path=path):
                    status, code, response = self.code(method, path, {} if method in {'POST', 'PUT', 'PATCH'} else None)
                    self.assertEqual((status, code, response.getheader('Allow')), (405, 'METHOD_NOT_ALLOWED', allow))
            self.assertEqual(self.request('PUT', '/api/run', {})[:2], (405, {'error': 'method_not_allowed'}))

    def test_start_run_body_validation(self):
        with environment():
            self.assertEqual(self.code('POST', '/api/workflow/run', {}, content_type='text/plain')[:2], (415, 'JSON_MIME_REQUIRED'))
            self.assertEqual(self.code('POST', '/api/workflow/run', raw=b'{"a":1,"a":2}')[:2], (400, 'MALFORMED_JSON'))
            self.assertEqual(self.code('POST', '/api/workflow/run', raw=b'{"a":NaN}')[:2], (400, 'MALFORMED_JSON'))
            self.assertEqual(self.code('POST', '/api/workflow/run', raw=b'not json')[:2], (400, 'MALFORMED_JSON'))
            self.assertEqual(self.code('POST', '/api/workflow/run', raw=b'')[:2], (400, 'BODY_REQUIRED'))
            self.assertEqual(self.code('POST', '/api/workflow/run', raw=b'{}', **{'Content-Length': '8388609'})[:2],
                             (413, 'BODY_TOO_LARGE'))

    def test_unsupported_contract_version_is_422_before_any_work(self):
        with environment(), patch.object(workflow_http, 'dispatch', new=self.real_dispatch), \
                patch.object(workflow_http, 'build_workflow', side_effect=AssertionError('no work')):
            for version in ('vogue-workflow/0.2', None, 'vogue-handoff/0.1'):
                with self.subTest(version=version):
                    status, code, _ = self.code('POST', '/api/workflow/run', {'contract_version': version, 'request_id': 'r-1'})
                    self.assertEqual((status, code), (422, 'UNSUPPORTED_CONTRACT_VERSION'))

    def test_unknown_run_is_a_404_problem(self):
        with environment():
            status, body, _ = self.request('GET', '/api/workflow/runs/missing-run')
            self.assertEqual((status, body['error']['code'], body['request_id']), (404, 'RUN_NOT_FOUND', 'missing-run'))
            status, body, _ = self.request('GET', '/api/workflow/runs/bad%2Fid')
            self.assertEqual((status, body['request_id']), (404, None))

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'workflow extra (jsonschema) required')
    def test_problem_bodies_match_the_common_schema(self):
        from vogue_orchestrator.workflow_contract import WorkflowContract
        validator = WorkflowContract().validators['common.schema.json'].evolve(
            schema={'$ref': 'https://contracts.vogue.invalid/workflow/0.1/common.schema.json#/$defs/problem'})
        with environment():
            for args in (('GET', '/api/workflow/nope'), ('POST', '/api/workflow/runs/x'), ('GET', '/api/workflow/runs/x'),
                         ('POST', '/api/workflow/run', None)):
                with self.subTest(args=args):
                    body = self.request(*args, raw=b'' if args[0] == 'POST' else None)[1]
                    self.assertTrue(validator.is_valid(body), body)


if __name__ == '__main__':
    unittest.main()
