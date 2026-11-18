"""Inbound trust at orchestrator (conventions section 5) and the container runtime mode."""
import contextlib
import email.message
import http.client
import json
import os
import tempfile
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fashion_planner_orchestrator.server import OrchestratorHandler, _request_is_trusted_local, runtime_mode, serve

TRUST_KEYS = ('ORCHESTRATOR_RUNTIME', 'ORCHESTRATOR_TRUSTED_PEER_CIDRS', 'ORCHESTRATOR_ALLOWED_HOSTS')


@contextlib.contextmanager
def environment(**values):
    with patch.dict(os.environ):
        for key in TRUST_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield


def handler(peer='127.0.0.1', port=8765, **headers):
    h = object.__new__(OrchestratorHandler)
    h.client_address = (peer, 50000)
    h.server = SimpleNamespace(server_port=port)
    h.headers = email.message.Message()
    for name, value in headers.items():
        for item in (value if isinstance(value, list) else [value]):
            h.headers[name] = item
    return h


class TrustedLocalTests(unittest.TestCase):
    def test_loopback_with_default_hosts_and_exact_origin(self):
        with environment():
            self.assertTrue(_request_is_trusted_local(handler(Host='127.0.0.1:8765')))
            self.assertTrue(_request_is_trusted_local(handler(Host='LOCALHOST:8765')))
            self.assertTrue(_request_is_trusted_local(handler(Host='127.0.0.1:8765', Origin='http://127.0.0.1:8765')))
            self.assertTrue(_request_is_trusted_local(handler('::ffff:127.0.0.1', Host='localhost:8765')))
            for headers in ({}, {'Host': 'evil.example:8765'}, {'Host': '127.0.0.1:9999'},
                            {'Host': '127.0.0.1:8765', 'Origin': 'http://127.0.0.1:9999'},  # the port now matters
                            {'Host': '127.0.0.1:8765', 'Origin': 'http://localhost:8765'},
                            {'Host': '127.0.0.1:8765', 'Origin': 'https://127.0.0.1:8765'},
                            {'Host': '127.0.0.1:8765', 'Origin': 'null'}, {'Host': '127.0.0.1:8765', 'Origin': ''},
                            {'Host': ['127.0.0.1:8765', 'evil.example']},
                            {'Host': '127.0.0.1:8765', 'Origin': ['http://127.0.0.1:8765', 'http://evil.example']}):
                with self.subTest(headers=headers):
                    self.assertFalse(_request_is_trusted_local(handler(**headers)))

    def test_trusted_peer_cidrs_and_allowed_hosts(self):
        good = dict(Host='127.0.0.1:18799', Origin='http://127.0.0.1:18799')
        with environment():
            self.assertFalse(_request_is_trusted_local(handler('172.30.231.1', **good)))
        with environment(ORCHESTRATOR_TRUSTED_PEER_CIDRS='172.30.231.0/28',
                         ORCHESTRATOR_ALLOWED_HOSTS='127.0.0.1:18799, localhost:18799'):
            self.assertTrue(_request_is_trusted_local(handler('172.30.231.1', **good)))
            self.assertTrue(_request_is_trusted_local(handler('172.30.231.14', Host='localhost:18799')))
            self.assertFalse(_request_is_trusted_local(handler('172.30.231.17', **good)))  # outside the /28
            self.assertFalse(_request_is_trusted_local(handler('172.30.231.1', Host='127.0.0.1:8765')))
            self.assertFalse(_request_is_trusted_local(handler('127.0.0.1', Host='127.0.0.1:8765')))
        with environment(ORCHESTRATOR_TRUSTED_PEER_CIDRS='172.30.231.0/28,not-a-cidr',
                         ORCHESTRATOR_ALLOWED_HOSTS='127.0.0.1:18799'):
            self.assertFalse(_request_is_trusted_local(handler('172.30.231.1', **good)))  # invalid list: loopback only
            self.assertTrue(_request_is_trusted_local(handler('127.0.0.1', **good)))

    def test_runtime_mode_and_startup_validation(self):
        self.assertEqual(runtime_mode({}), 'host')
        self.assertEqual(runtime_mode({'ORCHESTRATOR_RUNTIME': 'host'}), 'host')
        self.assertEqual(runtime_mode({'ORCHESTRATOR_RUNTIME': 'container'}), 'container')
        self.assertEqual(runtime_mode({'ORCHESTRATOR_RUNTIME': 'contianer'}), 'container')  # fails closed
        with environment(ORCHESTRATOR_RUNTIME='docker'), self.assertRaisesRegex(ValueError, 'RUNTIME'):
            serve()


class LiveServerTests(unittest.TestCase):
    """A real ThreadingHTTPServer on 127.0.0.1:0; the client is loopback, so Host/Origin decide trust."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), OrchestratorHandler)
        self.port = self.server.server_port
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            data = None if body is None else json.dumps(body).encode()
            if data is not None:
                headers.setdefault('Content-Type', 'application/json')
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw.startswith((b'{', b'[')) else raw)
        finally:
            connection.close()

    def test_host_mode_gates_workflow_routes_and_removes_legacy_routes(self):
        with environment():
            status, body = self.request('POST', '/api/workflow/run', {}, Origin='http://127.0.0.1:1')
            self.assertEqual((status, body['error']['code']), (403, 'LOCAL_WORKFLOW_ONLY'))
            self.assertEqual(self.request('POST', '/v1/orchestrations', {'contract_version': 'fashion-planner-workflow'},
                                          Host='evil.example')[0], 403)
            self.assertEqual(self.request('POST', '/api/run', {'query': 'x'})[0], 404)
            self.assertEqual(self.request('GET', '/api/run-history')[0], 404)
            self.assertEqual(self.request('GET', '/api/connections')[0], 404)

    def test_container_mode_gates_api_routes_but_keeps_health_and_static_files(self):
        evil = {'Host': 'evil.example:%d' % self.port}
        with environment(ORCHESTRATOR_RUNTIME='container'):
            self.assertEqual(self.request('GET', '/api/health', **evil),
                             (200, {'status': 'ok', 'mode': 'workflow'}))
            self.assertEqual(self.request('GET', '/', **evil)[0], 200)
            for method, path, body in (('GET', '/api/connections', None),
                                       ('POST', '/api/run', {'query': 'x'}),
                                       ('POST', '/v1/orchestrations', {'input': {}}),
                                       ('POST', '/api/health', {})):
                with self.subTest(method=method, path=path):
                    status, value = self.request(method, path, body, **evil)
                    self.assertEqual(status, 403)
                    if path == '/v1/orchestrations':  # workflow routes answer with the workflow problem body
                        self.assertEqual(value['error']['code'], 'LOCAL_WORKFLOW_ONLY')
                    else:
                        self.assertEqual(value, {'error': 'trusted_local_only'})
            self.assertEqual(self.request('GET', '/api/workflow/runs/x', **evil)[1]['error']['code'],
                             'LOCAL_WORKFLOW_ONLY')
            self.assertEqual(self.request('GET', '/api/workflow/runs/missing')[0], 404)


if __name__ == '__main__':
    unittest.main()
