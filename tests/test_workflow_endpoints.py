"""Trusted-local endpoints: run listing, append-only MD decisions, system status."""
import json
import socket
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

from _http_support import LiveServerMixin, environment
from vogue_orchestrator.workflow_store import WorkflowStore, digest

TOKEN = 'dummy-status-token'


def envelope(rid, status='ok', marker=0):
    return {'http_status': 200, 'envelope': {'contract_version': 'vogue-workflow/0.1', 'request_id': rid,
                                              'producer': {'agent_id': 'vogue-orchestrator', 'schema_version': 'integrated-result/1.0'},
                                              'status': status, 'result': {'request_id': rid, 'marker': marker,
                                                                           'user_input': {'text': 'legacy query ' + rid}}}}


class EndpointTestBase(LiveServerMixin):
    def setUp(self):
        self.start_server()
        self.store_dir = self.state / 'workflow'
        self.store = WorkflowStore(self.store_dir)

    def call(self, method, path, body=None, **kwargs):
        with environment(VOGUE_WORKFLOW_STORE_DIR=str(self.store_dir)):
            return self.request(method, path, body, **kwargs)[:2]

    def run_record(self, rid, text, *, final='ok'):
        self.store.bind(rid, digest({'rid': rid}))
        self.store.index_run(rid, text)
        self.store.save_run(rid, 'progress', envelope(rid, 'ok'))
        if final:
            self.store.save_run(rid, 'initial', envelope(rid, final))
            self.store.save_run(rid, 'current', envelope(rid, final))


class RunListingTests(EndpointTestBase, unittest.TestCase):
    def test_lists_newest_first_with_excerpts_and_statuses(self):
        self.run_record('run-1', 'first   query\nwith  whitespace')
        self.run_record('run-2', 'x' * 300, final='partial')
        self.run_record('run-3', 'still running', final=None)
        self.store.bind('run-legacy', 'h')  # recorded before the run index existed
        self.store.save_run('run-legacy', 'current', envelope('run-legacy', 'error'))
        status, body = self.call('GET', '/api/workflow/runs')
        self.assertEqual((status, body['limit']), (200, 20))
        runs = body['runs']
        self.assertEqual([r['request_id'] for r in runs], ['run-3', 'run-2', 'run-1', 'run-legacy'])
        self.assertEqual([r['status'] for r in runs], ['in_progress', 'partial', 'ok', 'error'])
        self.assertEqual(set(runs[0]), {'request_id', 'status', 'created_at', 'updated_at', 'query_excerpt'})
        self.assertEqual(runs[2]['query_excerpt'], 'first query with whitespace')
        self.assertEqual((len(runs[1]['query_excerpt']), runs[1]['query_excerpt'][-1]), (120, '…'))
        self.assertEqual((runs[3]['created_at'], runs[3]['query_excerpt']), (None, 'legacy query run-legacy'))
        self.assertLessEqual(runs[2]['created_at'], runs[2]['updated_at'])

    def test_limit_is_bounded_and_validated(self):
        for index in range(3):
            self.run_record('run-%d' % index, 'q')
        self.assertEqual([r['request_id'] for r in self.call('GET', '/api/workflow/runs?limit=2')[1]['runs']], ['run-2', 'run-1'])
        self.assertEqual(self.call('GET', '/api/workflow/runs?limit=1000')[1]['limit'], 100)
        for query in ('limit=0', 'limit=-1', 'limit=abc', 'limit=', 'limit=2&limit=3', 'limit=%D9%A3', 'limit=1.5'):
            with self.subTest(query=query):
                status, body = self.call('GET', '/api/workflow/runs?' + query)
                self.assertEqual((status, body['error']['code']), (422, 'LIMIT_INVALID'))


class MdDecisionTests(EndpointTestBase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.run_record('run-1', 'query')
        self.sha = digest(self.store.run('run-1', 'current')['envelope'])
        self.path = '/api/workflow/runs/run-1/md-decision'

    def decision(self, **overrides):
        body = {'contract_version': 'vogue-workflow/0.1', 'request_id': 'run-1', 'integrated_response_sha256': self.sha,
                'decision': 'adopt'}
        body.update(overrides)
        return {k: v for k, v in body.items() if v is not ...}

    def test_append_and_history(self):
        status, first = self.call('POST', self.path, self.decision(note='MD 검토: 채택'))
        self.assertEqual(status, 201)
        self.assertEqual({k: first[k] for k in ('request_id', 'sequence', 'integrated_response_sha256', 'decision', 'note')},
                         {'request_id': 'run-1', 'sequence': 1, 'integrated_response_sha256': self.sha, 'decision': 'adopt',
                          'note': 'MD 검토: 채택'})
        status, second = self.call('POST', self.path, self.decision(decision='hold', note=None))
        self.assertEqual((status, second['sequence'], second['note']), (201, 2, None))
        status, history = self.call('GET', self.path)
        self.assertEqual(status, 200)
        self.assertEqual(set(history), {'request_id', 'integrated_response_sha256', 'decisions'})
        self.assertEqual((history['request_id'], history['integrated_response_sha256']), ('run-1', self.sha))
        self.assertEqual(history['decisions'], [first, second])

    def test_strict_validation(self):
        cases = [(self.decision(extra=True), 'MD_DECISION_UNKNOWN_FIELD'),
                 (self.decision(contract_version='vogue-workflow/0.2'), 'UNSUPPORTED_CONTRACT_VERSION'),
                 (self.decision(contract_version=...), 'UNSUPPORTED_CONTRACT_VERSION'),
                 (self.decision(request_id='run-2'), 'REQUEST_ID_MISMATCH'),
                 (self.decision(integrated_response_sha256='ABC'), 'INTEGRATED_RESPONSE_SHA256_INVALID'),
                 (self.decision(integrated_response_sha256=...), 'INTEGRATED_RESPONSE_SHA256_INVALID'),
                 (self.decision(decision='approve'), 'MD_DECISION_INVALID'), (self.decision(decision=...), 'MD_DECISION_INVALID'),
                 (self.decision(decision=['adopt']), 'MD_DECISION_INVALID'),
                 (self.decision(note='x' * 2001), 'MD_NOTE_INVALID'), (self.decision(note=7), 'MD_NOTE_INVALID'),
                 (['adopt'], 'MD_DECISION_INVALID')]
        for body, code in cases:
            with self.subTest(code=code, body=body):
                status, problem = self.call('POST', self.path, body)
                self.assertEqual((status, problem['error']['code']), (422, code))
        self.assertEqual(self.call('POST', self.path, self.decision(note='x' * 2000))[0], 201)
        self.assertEqual(self.call('POST', self.path, raw=b'{"decision":"adopt",')[0], 400)
        self.assertEqual(self.call('POST', self.path, self.decision(), content_type='text/plain')[0], 415)
        self.assertEqual(self.call('POST', self.path, raw=b'{}', **{'Content-Length': '65537'})[0], 413)

    def test_state_conflicts(self):
        status, problem = self.call('POST', self.path, self.decision(integrated_response_sha256='0' * 64))
        self.assertEqual((status, problem['error']['code']), (409, 'INTEGRATED_RESPONSE_MISMATCH'))
        self.run_record('run-2', 'running', final=None)
        status, problem = self.call('POST', '/api/workflow/runs/run-2/md-decision', self.decision(request_id='run-2'))
        self.assertEqual((status, problem['error']['code']), (409, 'RUN_NOT_COMPLETE'))
        self.assertEqual(self.call('GET', '/api/workflow/runs/run-2/md-decision')[1],
                         {'request_id': 'run-2', 'integrated_response_sha256': None, 'decisions': []})
        for method in ('GET', 'POST'):
            with self.subTest(method=method):
                status, problem = self.call(method, '/api/workflow/runs/absent/md-decision',
                                            self.decision(request_id='absent') if method == 'POST' else None)
                self.assertEqual((status, problem['error']['code']), (404, 'RUN_NOT_FOUND'))

    def test_decisions_follow_the_current_integrated_response(self):
        self.assertEqual(self.call('POST', self.path, self.decision())[0], 201)
        self.store.save_run('run-1', 'current', envelope('run-1', 'ok', marker=1))  # e.g. after an image retry
        new_sha = digest(self.store.run('run-1', 'current')['envelope'])
        self.assertEqual(self.call('GET', self.path)[1]['integrated_response_sha256'], new_sha)
        self.assertEqual(self.call('POST', self.path, self.decision())[0], 409)
        status, record = self.call('POST', self.path, self.decision(integrated_response_sha256=new_sha, decision='revise'))
        self.assertEqual((status, record['sequence']), (201, 2))

    def test_history_is_append_only_and_concurrent_appends_get_distinct_sequences(self):
        errors = []

        def post():
            try:
                self.store.add_md_decision('run-1', self.sha, 'hold', None)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=post) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual([d['sequence'] for d in self.store.md_decisions('run-1')], [1, 2, 3, 4, 5, 6])
        for statement in ("UPDATE md_decisions SET decision='adopt'", 'DELETE FROM md_decisions'):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                with self.store.db() as db:
                    db.execute(statement)
        self.assertEqual(len(self.store.md_decisions('run-1')), 6)


class FakeAgent(BaseHTTPRequestHandler):
    statuses = {}
    seen = []

    def log_message(self, *args):
        return

    def do_GET(self):
        FakeAgent.seen.append((self.server.server_port, self.path, self.headers.get('Authorization')))
        required = FakeAgent.statuses.get(self.server.server_port)
        status = required if isinstance(required, int) else (200 if self.headers.get('Authorization') == 'Bearer ' + TOKEN else 401)
        body = b'{"status":"live","secret_echo":"' + TOKEN.encode() + b'"}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SystemStatusTests(EndpointTestBase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from _baseline_support import pin_proposed_baseline
        pin_proposed_baseline(self)

    def agent(self, status):
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeAgent)
        FakeAgent.statuses[server.server_port] = status
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_probes_report_liveness_without_secrets(self):
        FakeAgent.seen = []
        trend, stock, new_product = self.agent(200), self.agent(503), self.agent('bearer')
        refused = socket.socket()
        refused.bind(('127.0.0.1', 0))
        closed_port = refused.getsockname()[1]
        refused.close()
        env = dict(VOGUE_WORKFLOW_STORE_DIR=str(self.store_dir), VOGUE_AGENT_HTTP_DEV_MODE='1', VOGUE_AGENT_SERVICE_BEARER_TOKEN=TOKEN,
                   VOGUE_TREND_AGENT_URL='http://127.0.0.1:%d' % trend, VOGUE_STOCK_AGENT_URL='http://127.0.0.1:%d' % stock,
                   VOGUE_NEW_PRODUCT_AGENT_URL='http://127.0.0.1:%d' % new_product,
                   VOGUE_SALES_MARKET_AGENT_URL='http://127.0.0.1:%d' % closed_port,
                   VOGUE_WORKFLOW_ALLOW_PROPOSED='1', VOGUE_WORKFLOW_IMAGES_ENABLED='1', VOGUE_WORKFLOW_IMAGE_PROVIDER='fixture',
                   VOGUE_QUERY_STRUCTURING_MODE='synthetic_fixture', OPENAI_API_KEY='dummy-openai-key')
        with environment(**env):
            status, body, _ = self.request('GET', '/api/system/status')
        self.assertEqual(status, 200)
        agents = {a['id']: a for a in body['agents']}
        self.assertEqual({k: (a['reachable'], a['healthy'], a['http_status'], a['error']) for k, a in agents.items()},
                         {'trend': (True, True, 200, None), 'stock': (True, False, 503, None),
                          'new-product': (True, True, 200, None), 'sales-market': (False, False, None, 'CONNECTION_REFUSED')})
        self.assertEqual([a['health_path'] for a in body['agents']], ['/health', '/health/live', '/api/health', '/api/health'])
        self.assertTrue(all(isinstance(a['latency_ms'], int) for a in body['agents']))
        sent = {port: auth for port, _, auth in FakeAgent.seen}
        self.assertEqual((sent[trend], sent[stock], sent[new_product]), (None, None, 'Bearer ' + TOKEN))
        self.assertEqual((body['contract']['state'], body['contract']['gate']), ('proposed', 'development'))
        self.assertEqual(body['image'], {'enabled': True, 'provider': 'fixture', 'retry_enabled': False, 'ready': True})
        self.assertEqual((body['query_structuring']['mode'], body['runtime']), ('synthetic_fixture', 'host'))
        self.assertTrue(body['outbound']['service_token_configured'])
        text = json.dumps(body)
        for secret in (TOKEN, 'dummy-openai-key', 'secret_echo'):
            self.assertNotIn(secret, text)

    def test_policy_rejections_timeouts_and_unconfigured_agents(self):
        silent = socket.socket()
        silent.bind(('127.0.0.1', 0))
        silent.listen(1)
        self.addCleanup(silent.close)
        env = dict(VOGUE_WORKFLOW_STORE_DIR=str(self.store_dir), VOGUE_TREND_AGENT_URL='http://trend:8768',
                   VOGUE_STOCK_AGENT_URL='http://127.0.0.1:%d' % silent.getsockname()[1], VOGUE_AGENT_HTTP_DEV_MODE='1',
                   VOGUE_ORCHESTRATOR_RUNTIME='container')
        with environment(**env), patch('vogue_orchestrator.system_status.PROBE_TIMEOUT_SECONDS', 0.3):
            status, body, _ = self.request('GET', '/api/system/status')
        self.assertEqual(status, 200)
        errors = {a['id']: a['error'] for a in body['agents']}
        self.assertEqual(errors, {'trend': 'URL_POLICY_REJECTED', 'stock': 'TIMEOUT', 'new-product': 'NOT_CONFIGURED',
                                  'sales-market': 'NOT_CONFIGURED'})
        self.assertEqual((body['runtime'], body['contract']['gate'], body['image']['provider'], body['draft']['mode']),
                         ('container', 'closed', 'planning_sheet', 'draft_with_visuals'))

    def test_status_is_trusted_local_only(self):
        with environment():
            status, body, _ = self.request('GET', '/api/system/status', Host='evil.example:%d' % self.port)
        self.assertEqual((status, body['error']['code']), (403, 'LOCAL_WORKFLOW_ONLY'))


if __name__ == '__main__':
    unittest.main()
