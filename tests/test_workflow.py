"""Independent fixture transport tests; no producer/provider network calls."""
import importlib.util
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from vogue_orchestrator.workflow import Workflow, WireResponse
from vogue_orchestrator.workflow_contract import WorkflowContract
from vogue_orchestrator.workflow_store import WorkflowStore, canonical, digest

FIXTURES = Path(__file__).parent / 'fixtures/workflow'


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'Install the workflow extra; validated separately with the existing Anaconda runtime.')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p08-workflow-')
        self.addCleanup(self.tmp.cleanup)
        # The store refuses symlinked parents; macOS temp dirs live under the /var -> /private/var link.
        self.store = WorkflowStore(os.path.realpath(self.tmp.name))
        self.contract = WorkflowContract()
        self.sample = json.loads((FIXTURES/'draft-request-text-ok.json').read_bytes())
        self.rid = self.sample['request_id']
        self.trace = []
        self.fail_stage = None
        self.raise_at = None  # (stage, exception) raised by the fake transport
        self.status_at = None  # (stage, HTTP status) returned by the fake transport
        self.mutate = lambda stage, value: value
        self.fail_image = False
        self.image_data = next((FIXTURES/'raw').glob('*.png')).read_bytes()
        self.workflow = self.build()

    def build(self):
        def call(stage, payload):
            self.trace.append(stage)
            if stage == self.fail_stage:
                return WireResponse(b'{"auth":"fixture denial"}', 401)
            if self.raise_at and self.raise_at[0] == stage:
                raise self.raise_at[1]
            if self.status_at and self.status_at[0] == stage:
                return WireResponse(b'{"error":"fixture upstream failure"}', self.status_at[1])
            if stage in {'trend','stock'}:
                value = copy.deepcopy(self.sample['input'][stage+'_response']['raw_response'])
            else:
                value = json.loads((FIXTURES/('draft-response-ok.json' if stage=='planning_draft' else 'review-response-ok.json')).read_bytes())
                if stage == 'planning_draft':
                    value['result']['source_request'] = copy.deepcopy(payload)
                    value['result']['source_request_sha256'] = digest(payload)
                    if payload['input']['stock_response']['raw_response']['status'] != 'ok':
                        value['status'] = value['result']['status'] = 'partial'
                else:
                    source = payload['input']['planning_draft']
                    value['result']['review']['draft_response_sha256'] = source['content_sha256']
                    value['result']['review']['reviewed_draft'] = source['raw_response']['result']['draft_identity']
                    value['result']['review_identity']['content_sha256'] = digest(value['result']['review'])
            value = self.mutate(stage, value)
            # Deliberately noncanonical wire format to test distinct raw/object hashes.
            return WireResponse(json.dumps(value, indent=2, ensure_ascii=False).encode())
        def image(payload):
            self.trace.append('final_image')
            self.assertEqual(self.trace[-2], 'sales_market_review') if self.trace.count('final_image') == 1 else None
            if self.fail_image:
                raise RuntimeError('fixture')
            return self.image_data, 'image/png'
        return Workflow(trend=lambda p: call('trend', p), stock=lambda p: call('stock', p),
                        draft=lambda p: call('planning_draft', p), review=lambda p: call('sales_market_review', p),
                        image=image, store=self.store, contract=self.contract)

    def run_flow(self, **options):
        request = self.sample['input']
        trend_query = dict(request_id=self.rid, original_query=request['user_input']['text'])
        return self.workflow.run(request_id=self.rid, user_input=request['user_input'], planning_request=request['planning_request'], trend_query=trend_query, **options)

    def test_order_hash_and_persistent_idempotency(self):
        result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'ok', result)
        self.assertEqual(self.trace, ['trend','stock','planning_draft','sales_market_review','final_image'])
        body = result['envelope']['result']
        self.assertEqual([s['stage'] for s in body['stages']], ['trend','stock','planning_draft','sales_market_review','integration','final_image'])
        self.contract.schema(result['envelope'], 'integrated-result.schema.json')
        self.contract.semantic(result['envelope'], self.rid, self.store)
        retained = body['sources']['trend']
        self.assertNotEqual(retained['content_sha256'], retained['raw_artifact']['sha256'])
        self.assertEqual(json.loads(self.store.verify(retained['raw_artifact'])), retained['raw_response'])
        self.workflow = self.build()
        self.assertEqual(self.run_flow(), result)
        self.assertEqual(len(self.trace), 5)

    def test_auth_every_stage_stops_and_preserves_bytes(self):
        for stage in ['trend','stock','planning_draft','sales_market_review']:
            with self.subTest(stage=stage):
                self.setUp()
                self.fail_stage = stage
                result = self.run_flow()
                self.assertEqual(result['http_status'], 401)
                self.assertEqual(result['envelope']['status'], 'error')
                self.assertEqual(self.trace[-1], stage)
                body = result['envelope']['result']
                self.assertEqual(self.store.verify(body['rejected_response_artifacts'][0]), b'{"auth":"fixture denial"}')
                self.assertIsNotNone(self.store.run(self.rid))

    def test_error_no_results_and_contract_stop(self):
        cases = [('trend','error',1), ('trend','no_results',1), ('planning_draft','no_results',3), ('sales_market_review','no_results',4)]
        for stage, status, calls in cases:
            with self.subTest(stage=stage,status=status):
                self.setUp()
                def mutate(s, v):
                    if s == stage:
                        if stage == 'trend':
                            v = json.loads((FIXTURES/'trend-error.json').read_bytes()) if status == 'error' else json.loads((FIXTURES/'integrated-trend-no-results.json').read_bytes())['result']['sources']['trend']['raw_response']
                        elif stage == 'planning_draft':
                            v['status'] = v['result']['status'] = status
                            v['result']['draft'] = v['result']['draft_identity'] = None
                            v['result']['limitations'] = ['No results fixture.']
                        else:
                            v['status'] = v['result']['status'] = status
                            v['result']['limitations'] = ['No results fixture.']
                            v['result']['review']['findings'] = []
                            for m in v['result']['review']['metrics']:
                                m['value'] = None
                                m['missing_reason'] = 'No results.'
                            v['result']['review_identity']['content_sha256'] = digest(v['result']['review'])
                    return v
                self.mutate = mutate
                result = self.run_flow()
                self.assertEqual(len(self.trace), calls, result)
                self.assertNotIn('final_image', self.trace)
                self.assertEqual(result['envelope']['result']['final_image']['provider_calls'], 0)

    def test_contract_mismatch_stops(self):
        for field in ['request_id','producer','status']:
            with self.subTest(field=field):
                self.setUp()
                def mutate(stage,v):
                    if stage == 'trend':
                        v[field] = 'invalid'
                    return v
                self.mutate = mutate
                result = self.run_flow()
                self.assertEqual(result['envelope']['status'], 'error')
                self.assertEqual(self.trace, ['trend'])

    def test_image_retry_does_not_rerun_producers(self):
        self.fail_image = True
        first = self.run_flow()
        self.assertEqual(first['envelope']['status'], 'partial')
        self.fail_image = False
        retried = self.workflow.retry_image(self.rid, 'retry-1')
        self.assertEqual(retried['envelope']['result']['final_image']['status'], 'generated')
        self.assertEqual(self.trace, ['trend','stock','planning_draft','sales_market_review','final_image','final_image'])
        self.assertEqual(self.workflow.retry_image(self.rid,'retry-1'), retried)
        self.assertEqual(len(self.trace), 6)
        self.assertEqual(self.store.run(self.rid), first)
        self.assertEqual(self.workflow.retry_image(self.rid,'retry-2'),retried)
        self.assertEqual(self.run_flow(),retried)
        self.assertEqual(len(self.trace),6)

    def test_text_and_image_reference_reaches_full_draft(self):
        image = self.store.upload(self.image_data, 'image/png', self.rid)
        self.sample['input']['user_input']['images'] = [image]
        result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'ok', result)
        draft = result['envelope']['result']['sources']['planning_draft']['raw_response']['result']
        self.assertEqual(draft['source_request']['input']['user_input']['images'], [image])
        with self.assertRaises(ValueError):
            self.store.verify(image, owner='other-request')
        with self.assertRaises(ValueError):
            self.store.upload(b'not png', 'image/png', self.rid)

    def test_no_results_stock_retains_null_slots_and_partial(self):
        def change(stage, value):
            if stage == 'stock':
                value = json.loads((FIXTURES/'stock-no-results.json').read_bytes())
            return value
        self.mutate = change
        result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'partial', result)
        self.assertEqual(result['envelope']['result']['sources']['stock']['raw_response']['result']['products'], [])
        self.assertEqual(self.trace[-1], 'final_image')

    def test_unconfigured_image_is_zero_calls_and_image_only_resume(self):
        image=self.workflow.image
        self.workflow.image=None
        result=self.run_flow()
        self.assertEqual(result['envelope']['status'],'partial')
        self.assertEqual(result['envelope']['result']['final_image']['provider_calls'],0)
        self.assertEqual(result['envelope']['result']['final_image']['status'],'skipped')
        self.workflow.image=image
        result=self.workflow.retry_image(self.rid,'configured-now')
        self.assertEqual(result['envelope']['result']['final_image']['status'],'generated')
        self.assertEqual(self.trace,['trend','stock','planning_draft','sales_market_review','final_image'])

    def test_fixture_image_provider_runs_offline_and_counts_one_call(self):
        import os
        from unittest.mock import patch
        from vogue_orchestrator.workflow_http import fixture_image
        self.workflow.image = fixture_image
        with patch.dict(os.environ, {'VOGUE_WORKFLOW_IMAGES_ENABLED': '1'}), \
                patch('socket.socket.connect', side_effect=AssertionError('no network')):
            result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'ok', result)
        image = result['envelope']['result']['final_image']
        self.assertEqual((image['status'], image['provider_calls'], image['artifact']['media_type']), ('generated', 1, 'image/png'))
        self.assertEqual(image['artifact']['uri'], 'artifact://p08/blobs/' + image['artifact']['sha256'])
        with patch.dict(os.environ, {'VOGUE_WORKFLOW_IMAGES_ENABLED': '1'}):
            expected, _ = fixture_image({'lineage': image['lineage']})
        self.assertEqual(self.store.verify(image['artifact']), expected)
        self.contract.schema(result['envelope'], 'integrated-result.schema.json')

    def test_duplicate_concurrent_requests_generate_once(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(lambda _: self.run_flow(), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(self.trace.count('final_image'), 1)

    def test_crash_unknown_checkpoint_does_not_reissue_upstream(self):
        request = self.sample['input']
        trend_query = dict(request_id=self.rid, original_query=request['user_input']['text'])
        payload = dict(contract_version='vogue-handoff/0.1', request_id=self.rid, input=trend_query)
        self.store.checkpoint_write(self.workflow._key(self.rid,'trend',payload),'started')
        response = self.run_flow()
        self.assertEqual(response['http_status'], 409)
        self.assertEqual(self.trace, [])
        self.assertEqual(response['envelope']['status'], 'error')

    def test_request_conflict_and_bad_image_rejected_before_producers(self):
        self.run_flow()
        self.sample['input']['user_input']['text'] = 'changed'
        self.sample['input']['planning_request']['query'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'REQUEST_ID_INPUT_CONFLICT'):
            self.run_flow()
        self.assertEqual(len(self.trace),5)
        self.setUp()
        self.sample['input']['user_input']['images'] = [dict(uri='file:///secret')]
        with self.assertRaises(ValueError): self.run_flow()
        self.assertEqual(self.trace,[])

    def test_review_revision_and_draft_hash_corruption_stop_image(self):
        for target in ['planning_draft','sales_market_review']:
            with self.subTest(stage=target):
                self.setUp()
                def mutate(stage,v):
                    if stage == target:
                        if stage == 'planning_draft': v['result']['draft_identity']['content_sha256'] = '0'*64
                        else:
                            v['result']['review']['reviewed_draft']['revision'] += 1
                            v['result']['review_identity']['content_sha256'] = digest(v['result']['review'])
                    return v
                self.mutate=mutate
                response=self.run_flow()
                self.assertEqual(response['envelope']['status'],'error')
                self.assertNotIn('final_image',self.trace)

    def test_transport_exceptions_follow_the_outbound_failure_mapping(self):
        import socket
        cases = [(TimeoutError('read'), 'UPSTREAM_TIMEOUT', 504, True),
                 (socket.timeout('read'), 'UPSTREAM_TIMEOUT', 504, True),  # QA-O-01: was 503
                 (ConnectionRefusedError(), 'UPSTREAM_UNAVAILABLE', 503, True),
                 (ConnectionResetError(), 'UPSTREAM_UNAVAILABLE', 503, True)]
        for exc, code, status, retryable in cases:
            with self.subTest(exc=type(exc).__name__):
                self.setUp()
                self.raise_at = ('stock', exc)
                result = self.run_flow()
                self.assertEqual(result['http_status'], status)
                error = result['envelope']['result']['errors'][-1]
                self.assertEqual((error['code'], error['stage'], error['retryable']), (code, 'stock', retryable))
                self.assertEqual(self.trace, ['trend', 'stock'])
                self.assertEqual(result['envelope']['result']['rejected_response_artifacts'], [])

    def test_upstream_non_2xx_statuses_follow_the_outbound_failure_mapping(self):
        # An upstream 503/504 keeps its meaning (the same codes as our own refused/timeout cases);
        # every other non-2xx is 502. The upstream body is retained privately in every case.
        cases = [(500, 'UPSTREAM_HTTP_ERROR', 502, False), (502, 'UPSTREAM_HTTP_ERROR', 502, False),
                 (503, 'UPSTREAM_UNAVAILABLE', 503, True), (504, 'UPSTREAM_TIMEOUT', 504, True),
                 (404, 'UPSTREAM_HTTP_ERROR', 502, False), (401, 'UPSTREAM_AUTH', 401, False),
                 (403, 'UPSTREAM_AUTH', 403, False)]
        for upstream, code, status, retryable in cases:
            with self.subTest(upstream=upstream):
                self.setUp()
                self.status_at = ('planning_draft', upstream)
                result = self.run_flow()
                self.assertEqual(result['http_status'], status)
                body = result['envelope']['result']
                error = body['errors'][-1]
                self.assertEqual((result['envelope']['status'], error['code'], error['stage'], error['retryable']),
                                 ('error', code, 'planning_draft', retryable))
                self.assertEqual(self.store.verify(body['rejected_response_artifacts'][0]), b'{"error":"fixture upstream failure"}')
                self.assertEqual(self.trace, ['trend', 'stock', 'planning_draft'])
                self.assertEqual(self.run_flow()['http_status'], status)  # replay returns the stored outcome
                self.assertEqual(len(self.trace), 3)

    @staticmethod
    def p07_reference(data):
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        return dict(artifact_id='p07-review-' + sha[:16], uri='artifact://p07/reviews/%s.json' % sha, sha256=sha,
                    media_type='application/json', size_bytes=len(data), immutable=True)

    def review_carries(self, reference):
        def mutate(stage, value):
            if stage == 'sales_market_review':
                value['result']['artifacts'] = [reference]
            return value
        self.mutate = mutate

    def peer_root(self, reference, data):
        root = Path(self.tmp.name) / 'peer'
        (root / 'p07' / 'reviews').mkdir(parents=True)
        (root / 'p07' / 'reviews' / (reference['sha256'] + '.json')).write_bytes(data)
        return root

    def test_review_peer_artifact_without_reader_is_503_artifact_reader_required(self):
        self.review_carries(self.p07_reference(b'{"review":"synthetic"}'))
        result = self.run_flow()
        body = result['envelope']['result']
        self.assertEqual((result['http_status'], body['errors'][-1]['code'], body['errors'][-1]['retryable']),
                         (503, 'ARTIFACT_READER_REQUIRED', True))
        self.assertEqual(self.trace, ['trend', 'stock', 'planning_draft', 'sales_market_review'])
        self.assertEqual(body['final_image']['provider_calls'], 0)

    def test_review_peer_artifact_is_imported_through_the_reader(self):
        from vogue_orchestrator.workflow_store import PeerArtifactReader
        data = b'{"review":"synthetic"}'
        reference = self.p07_reference(data)
        self.workflow.artifact_reader = PeerArtifactReader(self.peer_root(reference, data))
        self.review_carries(reference)
        result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'ok', result)
        self.assertEqual(self.store.verify(reference), data)
        self.contract.schema(result['envelope'], 'integrated-result.schema.json')
        self.contract.semantic(result['envelope'], self.rid, self.store)

    def test_review_peer_artifact_mismatch_fails_closed(self):
        from vogue_orchestrator.workflow_store import PeerArtifactReader
        reference = self.p07_reference(b'{"review":"synthetic"}')
        self.workflow.artifact_reader = PeerArtifactReader(self.peer_root(reference, b'{"review":"SYNTHETIC"}'))  # same size, other bytes
        self.review_carries(reference)
        result = self.run_flow()
        self.assertEqual((result['http_status'], result['envelope']['result']['errors'][-1]['code']),
                         (422, 'ARTIFACT_HASH_MISMATCH'))
        self.assertNotIn('final_image', self.trace)

    def test_run_level_limitations_are_appended_to_the_result(self):
        note = 'workflow-contract/0.1.1 not frozen; development run'
        self.workflow = Workflow(**{k: self.workflow.adapters[s] for k, s in (('trend', 'trend'), ('stock', 'stock'),
                                    ('draft', 'planning_draft'), ('review', 'sales_market_review'))},
                                 image=self.workflow.image, store=self.store, contract=self.contract, limitations=[note])
        result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'ok', result)
        self.assertEqual(result['envelope']['result']['limitations'].count(note), 1)
        self.contract.schema(result['envelope'], 'integrated-result.schema.json')

    def test_strict_json_and_symlink_store_rejection(self):
        from vogue_orchestrator.workflow_store import strict_json
        for raw in [b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}']:
            with self.assertRaises(ValueError): strict_json(raw)
        with self.assertRaises(ValueError): canonical({1:'bad'})
        link=Path(self.tmp.name)/'symlink'
        link.symlink_to(self.store.blobs, target_is_directory=True)
        with self.assertRaises(ValueError): WorkflowStore(link)

if __name__ == '__main__':
    unittest.main()
