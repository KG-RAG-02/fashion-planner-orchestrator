"""Outbound HTTP policy from P08 (conventions section 4). Loopback sockets only; no network."""
import importlib.util
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from fashion_planner_orchestrator.connectors import (ConnectorConfigurationError, _service_url, private_http_hosts, timeout_seconds)
from fashion_planner_orchestrator.workflow import StageFailure, stage_failure

TOKEN = 'dummy-test-token'
PRIVATE = {'ORCHESTRATOR_HTTP_PRIVATE_HOSTS': 'trend,inventory,novelty,research',
           'FASHION_PLANNER_SERVICE_TOKEN': TOKEN}


def url(value, **env):
    return _service_url({'X': value, **env}, 'X', '/v1/agents/x')


class PrivateHostAllowlistTests(unittest.TestCase):
    def test_plain_http_to_an_allowlisted_host_needs_a_bearer_token(self):
        self.assertEqual(url('http://trend:8768', **PRIVATE), 'http://trend:8768/v1/agents/x')
        self.assertEqual(url('http://novelty:8767/', **PRIVATE), 'http://novelty:8767/v1/agents/x')
        for token in ('', '   '):
            with self.subTest(token=repr(token)), self.assertRaisesRegex(ConnectorConfigurationError, 'TOKEN'):
                url('http://trend:8768', **dict(PRIVATE, FASHION_PLANNER_SERVICE_TOKEN=token))

    def test_allowlist_is_exact_and_independent_of_dev_mode(self):
        with self.assertRaises(ConnectorConfigurationError):
            url('http://trend:8768', FASHION_PLANNER_HTTP_DEV_MODE='1', FASHION_PLANNER_SERVICE_TOKEN=TOKEN)
        self.assertEqual(url('http://inventory:8002', FASHION_PLANNER_HTTP_DEV_MODE='1', **PRIVATE),
                         'http://inventory:8002/v1/agents/x')
        for target in ('http://trends:8768', 'http://trend.internal:8768', 'http://172.30.231.2:8768',
                       'http://evil:80'):
            with self.subTest(target=target), self.assertRaises(ConnectorConfigurationError):
                url(target, **PRIVATE)

    def test_allowlist_entries_must_be_single_label_lowercase_hostnames(self):
        self.assertEqual(private_http_hosts({'ORCHESTRATOR_HTTP_PRIVATE_HOSTS': ' a , novelty-api,,' + 'b' * 63}),
                         frozenset({'a', 'novelty-api', 'b' * 63}))
        for bad in ('Trend', 'trend.local', '10.0.0.1', '-x', '1abc', 'b' * 64, 'tr_end', 'trend:8768'):
            with self.subTest(bad=bad), self.assertRaises(ConnectorConfigurationError):
                private_http_hosts({'ORCHESTRATOR_HTTP_PRIVATE_HOSTS': bad})

    def test_existing_policy_is_unchanged_for_other_hosts(self):
        self.assertEqual(url('https://agents.example.invalid'), 'https://agents.example.invalid/v1/agents/x')
        self.assertEqual(url('http://127.0.0.1:8768', FASHION_PLANNER_HTTP_DEV_MODE='1'), 'http://127.0.0.1:8768/v1/agents/x')
        for target, env in (('http://127.0.0.1:8768', {}), ('http://example.com', {'FASHION_PLANNER_HTTP_DEV_MODE': '1'}),
                            ('http://user:pw@trend:8768', PRIVATE), ('http://trend:8768/prefix', PRIVATE),
                            ('http://trend:99999', PRIVATE)):
            with self.subTest(target=target), self.assertRaises(ConnectorConfigurationError):
                url(target, **env)

class TimeoutConfigurationTests(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(timeout_seconds({}, 'T', 75), 75.0)
        self.assertEqual(timeout_seconds({'T': ' 2.5 '}, 'T', 75), 2.5)
        for bad in ('0', '-1', 'abc', 'nan', 'inf', '601'):
            with self.subTest(bad=bad), self.assertRaises(ConnectorConfigurationError):
                timeout_seconds({'T': bad}, 'T', 75)

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'workflow extra (jsonschema) required')
    def test_build_workflow_uses_private_hosts_and_configured_timeouts(self):
        from fashion_planner_orchestrator import workflow_http
        from fashion_planner_orchestrator.workflow_store import WorkflowStore
        env = {**PRIVATE, 'ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED': '1', 'ORCHESTRATOR_TREND_URL': 'http://trend:8768',
               'ORCHESTRATOR_INVENTORY_URL': 'http://inventory:8002', 'ORCHESTRATOR_NOVELTY_URL': 'http://novelty:8767',
               'ORCHESTRATOR_RESEARCH_URL': 'http://research:8766',
               'ORCHESTRATOR_WORKFLOW_CONNECT_TIMEOUT_SECONDS': '1.5', 'ORCHESTRATOR_WORKFLOW_READ_TIMEOUT_SECONDS': '9'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env), \
                patch.object(workflow_http, 'store', return_value=WorkflowStore(Path(os.path.realpath(tmp)) / 's')), \
                patch.object(workflow_http, 'post_wire', return_value='sent') as wire:
            os.environ.pop('FASHION_PLANNER_HTTP_DEV_MODE', None)
            os.environ.pop('ORCHESTRATOR_WORKFLOW_CONTRACT_BASELINE', None)
            workflow = workflow_http.build_workflow()
            self.assertEqual(workflow.adapters['stock']({'p': 1}), 'sent')
        wire.assert_called_once_with('http://inventory:8002/v1/agents/inventory/runs', {'p': 1}, TOKEN,
                                     connect_timeout=1.5, read_timeout=9.0)

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'workflow extra (jsonschema) required')
    def test_invalid_timeout_is_a_409_configuration_problem(self):
        from unittest.mock import Mock
        from fashion_planner_orchestrator.server import OrchestratorHandler
        from fashion_planner_orchestrator.workflow_http import dispatch
        handler = object.__new__(OrchestratorHandler)
        handler._json = Mock()
        env = {**PRIVATE, 'ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED': '1', 'ORCHESTRATOR_TREND_URL': 'http://trend:8768',
               'ORCHESTRATOR_INVENTORY_URL': 'http://inventory:8002', 'ORCHESTRATOR_NOVELTY_URL': 'http://novelty:8767',
               'ORCHESTRATOR_RESEARCH_URL': 'http://research:8766', 'ORCHESTRATOR_WORKFLOW_READ_TIMEOUT_SECONDS': 'never'}
        request = dict(contract_version='fashion-planner-workflow', request_id='r-1',
                       input=dict(user_input=dict(text='t', images=[])))
        with patch.dict(os.environ, env):
            os.environ.pop('ORCHESTRATOR_WORKFLOW_CONTRACT_BASELINE', None)
            dispatch(handler, request)
        self.assertEqual(handler._json.call_args.args[0], 409)


class WireTransportTests(unittest.TestCase):
    """post_wire against loopback sockets: the transport itself maps timeouts and refusals."""

    def test_post_wire_reports_transport_elapsed_time(self):
        from types import SimpleNamespace
        from fashion_planner_orchestrator.workflow_http import post_wire
        response = SimpleNamespace(status=200, read=lambda limit: b'{}',
                                   getheader=lambda name, default: 'application/json')
        connection = SimpleNamespace(sock=SimpleNamespace(settimeout=lambda value: None),
                                     connect=lambda: None, request=lambda *args, **kwargs: None,
                                     getresponse=lambda: response, close=lambda: None)
        with patch('fashion_planner_orchestrator.workflow_http.http.client.HTTPConnection', return_value=connection):
            wire = post_wire('http://127.0.0.1:8768/v1/test', {'safe': True}, connect_timeout=1, read_timeout=2)
        self.assertEqual((wire.body, wire.status, wire.media_type, wire.truncated),
                         (b'{}', 200, 'application/json', False))
        self.assertIsInstance(wire.elapsed_ms, float)
        self.assertGreaterEqual(wire.elapsed_ms, 0)

    def listener(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(4)
        self.addCleanup(server.close)
        return server

    def test_read_timeout_is_504_upstream_timeout(self):
        from fashion_planner_orchestrator.workflow_http import post_wire
        server = self.listener()  # the kernel completes the handshake; nobody ever answers
        with self.assertRaises(StageFailure) as raised:
            post_wire('http://127.0.0.1:%d/v1/x' % server.getsockname()[1], {'a': 1}, TOKEN,
                      connect_timeout=1, read_timeout=0.2)
        self.assertEqual((raised.exception.code, raised.exception.http_status), ('UPSTREAM_TIMEOUT', 504))

    def test_refused_and_reset_connections_are_503_upstream_unavailable(self):
        from fashion_planner_orchestrator.workflow_http import post_wire
        released = socket.socket()
        released.bind(('127.0.0.1', 0))
        refused_port = released.getsockname()[1]
        released.close()  # nothing owns the port now: connections are refused (a bound, unlistened port drops SYNs on macOS)
        server = self.listener()

        def reset():
            conn, _ = server.accept()
            conn.recv(65536)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))  # close with RST
            conn.close()
        worker = threading.Thread(target=reset, daemon=True)
        worker.start()
        for port in (refused_port, server.getsockname()[1]):
            with self.subTest(port=port), self.assertRaises(StageFailure) as raised:
                post_wire('http://127.0.0.1:%d/v1/x' % port, {'a': 1}, None, connect_timeout=1, read_timeout=2)
            self.assertEqual((raised.exception.code, raised.exception.http_status), ('UPSTREAM_UNAVAILABLE', 503))
        worker.join(2)

    def test_stage_failure_mapping_matches_the_conventions(self):
        import ssl
        artifact = {'uri': 'artifact://orchestrator/blobs/' + '0' * 64}
        cases = [(TimeoutError(), None, 'UPSTREAM_TIMEOUT', 504), (socket.timeout(), None, 'UPSTREAM_TIMEOUT', 504),
                 (ConnectionRefusedError(), None, 'UPSTREAM_UNAVAILABLE', 503),
                 (ssl.SSLCertVerificationError(), None, 'UPSTREAM_UNAVAILABLE', 503),
                 (ValueError('DUPLICATE_JSON_KEY'), artifact, 'CONTRACT_VIOLATION', 502),
                 (KeyError('result'), artifact, 'CONTRACT_VIOLATION', 502),
                 (RuntimeError('adapter'), None, 'UPSTREAM_UNAVAILABLE', 503)]
        for exc, art, code, status in cases:
            with self.subTest(exc=type(exc).__name__):
                failure = stage_failure(exc, art)
                self.assertEqual((failure.code, failure.http_status, failure.artifact), (code, status, art))



if __name__ == '__main__':
    unittest.main()
