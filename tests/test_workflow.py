"""Independent fixture transport tests; no producer/provider network calls."""
import importlib.util
import copy
import logging
import json
import os
from pathlib import Path
import tempfile
import unittest
from vogue_orchestrator.workflow import Workflow, WireResponse
from vogue_orchestrator.workflow_contract import WorkflowContract
from vogue_orchestrator.workflow_store import WorkflowStore, canonical, digest

FIXTURES = Path(__file__).parent / 'fixtures/workflow'
CONTRACT_FIXTURES = Path(__file__).resolve().parents[1] / 'tools/workflow_contract/fixtures'


def consumer_projection_p07_review(request_id, product):
    """P07 main c662d4d's response shape (its consumer projection; a fabricated fixture) for this run and product."""
    value = json.loads((CONTRACT_FIXTURES / 'research-handoff.consumer-projection.response.json').read_bytes())
    value['request_id'] = value['result']['request_id'] = request_id
    value['result']['draft_context'] = copy.deepcopy(product)
    return value


def add_second_trend_style(sample, style_id='fixture:style-2', name='합성 블라우스'):
    """Give the sample run a second Trend style and the stock slot stock-agent returns for it (products[i] ↔
    trend_results[i]); the stock provenance hash follows the changed Trend result. Returns the new style."""
    trend = sample['input']['trend_response']['raw_response']['result']
    second = copy.deepcopy(trend['trends'][0])
    second['style'].update(id=style_id, name=name)
    for item in second['items']:
        item['id'] += '-2'
    trend['trends'].append(second)
    stock = sample['input']['stock_response']['raw_response']['result']
    evidence = dict(copy.deepcopy(stock['evidence'][0]), evidence_id=stock['evidence'][0]['evidence_id'] + '-2',
                    style_id=style_id)
    stock['evidence'].append(evidence)
    stock['products'].append(dict(copy.deepcopy(stock['products'][0]), style_id=style_id, sku_ids=['fixture:sku-2']))
    stock['trend_results'].append(dict(copy.deepcopy(stock['trend_results'][0]), style={'id': style_id, 'name': name},
                                       product_index=len(stock['products']) - 1, evidence_ids=[evidence['evidence_id']]))
    stock['input_provenance']['source_payload_sha256'] = digest(trend)
    return second['style']


def native_p07_review(request_id, product, status='ok'):
    return {
        'contract_version': 'research-handoff',
        'request_id': request_id,
        'producer': {'agent_id': 'research', 'schema_version': '1.0.0'},
        'status': status,
        'result': {
            'schema_version': '1.0.0', 'request_id': request_id, 'draft_id': None,
            'producer_id': 'research', 'status': status, 'draft_context': copy.deepcopy(product),
            'category_price_comparison': [], 'material_cost_analysis': [], 'customer_segment_analysis': [],
            'target_audience_analysis': {}, 'margin_estimate': {}, 'evidence': [], 'missing_data': [],
            'errors': [], 'limitations': ['synthetic P07 response'],
            'decision_support': {'recommendation': 'review_required', 'ready_for_human_decision': True,
                                 'evidence_areas': {'category_price': False, 'material_cost': False,
                                                    'margin': False, 'customer_segments': False},
                                 'human_decision_required': True},
            'price_analysis': {'status': 'no_results', 'proposed_price': None}, 'fabric_cost_analysis': {},
            'margin_analysis': {}, 'margin_price_range_analysis': None, 'target_audience': {},
        },
    }


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
        # These fakes answer with Draft-only responses; draft_with_visuals runs are in DraftVisualsWorkflowTests.
        self.draft_mode = 'draft_only'
        self.artifact_reader = None
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
                if stage == 'planning_draft':
                    value = json.loads((FIXTURES/'draft-response-ok.json').read_bytes())
                    value['contract_version'] = 'novelty-handoff'
                    value['producer']['schema_version'] = '1.0.0'
                    value['result']['schema_version'] = '1.0.0'
                    value['result']['input_evidence'] = value['result'].get('input_evidence', {})
                    value['result'].pop('source_request', None)
                    value['result'].pop('source_request_sha256', None)
                    if payload['input']['inventory_product_result']['status'] != 'ok':
                        value['status'] = value['result']['status'] = 'partial'
                else:
                    value = native_p07_review(self.rid, payload['input']['product'])
            value = self.mutate(stage, value)
            if stage == 'planning_draft':
                self._last_draft_response = copy.deepcopy(value)
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
                        image=image, store=self.store, contract=self.contract, draft_mode=self.draft_mode,
                        artifact_reader=self.artifact_reader)

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

    def test_current_p06_p07_native_contracts_run_in_sequence(self):
        calls = {}
        def p06(payload):
            calls['p06'] = copy.deepcopy(payload)
            product = {'category':'Outerwear','product_type':'Jacket','gender':'women','season':'winter','material':'wool'}
            calls['p06_product'] = product
            profile = {key:{'value':value,'kind':'user_requested','evidence_ids':[]} for key,value in product.items()}
            result = {'schema_version':'1.0.0','request_id':self.rid,'status':'partial',
                      'draft':{'title':'검증용 Draft','category':'Outerwear','season':'winter','product_profile':profile,
                               'opportunity':{'value':'합성 기회','kind':'interpretation','evidence_ids':[]},
                               'product_definition':{'value':'합성 재킷','kind':'proposal','evidence_ids':[]},
                               'attributes':[],'limitations':['synthetic P06 response']},'evidence_ids':[],
                      'input_evidence':{},'limitations':['synthetic P06 response'],'errors':[]}
            return WireResponse(json.dumps({'contract_version':'novelty-handoff','request_id':self.rid,
                'producer':{'agent_id':'novelty','schema_version':'1.0.0'},'status':'partial','result':result}).encode())
        def p07(payload):
            calls['p07'] = copy.deepcopy(payload)
            product = payload['input']['product']
            return WireResponse(json.dumps(native_p07_review(self.rid, product)).encode())
        self.workflow.adapters['planning_draft'] = p06
        self.workflow.adapters['sales_market_review'] = p07
        self.workflow.image = None
        result = self.run_flow()['envelope']
        self.assertEqual(result['contract_version'], 'fashion-planner-workflow')
        self.assertEqual(calls['p06']['contract_version'], 'novelty-handoff')
        self.assertEqual(set(calls['p06']['input']), {'planning_request','trend_result','inventory_product_result'})
        self.assertEqual(calls.get('p07', {}).get('contract_version'), 'research-handoff', result)
        self.assertEqual(set(calls['p07']['input']), {'request_id', 'product', 'product_evidence'})
        self.assertEqual(calls['p07']['input']['product'], {'category':'Outerwear','product_type':'Jacket','gender':'women','season':'winter','material':'wool'})
        self.assertEqual(calls['p07']['input']['product_evidence'], {
            key: {'kind': 'user_requested', 'source_ref': f'novelty.draft.product_profile.{key}', 'evidence_ids': []}
            for key in ('category', 'product_type', 'gender', 'season', 'material')})
        self.assertEqual(result['result']['sources']['planning_draft']['raw_response']['result']['schema_version'], '1.0.0')
        self.assertEqual(result['result']['sources']['sales_market_review']['raw_response']['result']['schema_version'], '1.0.0')
        self.assertTrue(any('does not echo an immutable' in item for item in result['result']['integration']['limitations']))

    def test_p07_http_error_code_is_preserved_as_a_fatal_stage_error(self):
        error = {'contract_version': 'research-handoff', 'request_id': self.rid,
                 'error': {'code': 'INVALID_INPUT', 'message': 'Unsupported product field.'}}
        self.workflow.adapters['sales_market_review'] = lambda _payload: WireResponse(
            json.dumps(error).encode(), status=422)
        result = self.run_flow()['envelope']
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['result']['errors'][-1]['code'], 'INVALID_INPUT')
        self.assertTrue(result['result']['errors'][-1]['fatal'])

    def test_p07_receives_only_sent_fields_with_their_p06_provenance(self):
        calls = []
        profile = {'category':{'value':'Outerwear','kind':'user_requested','evidence_ids':[]},
                   'material':{'value':'wool blend','kind':'evidence_observation','evidence_ids':['trend:e1','trend:e1']},
                   'season':{'value':'winter','kind':'proposal','evidence_ids':[]},
                   **{key:{'value':None,'kind':'unknown','evidence_ids':[]} for key in ('product_type','gender')}}
        draft_result = {'schema_version':'1.0.0','request_id':self.rid,'status':'partial','evidence_ids':['trend:e1'],
                        'draft':{'category':'Outerwear','product_profile':profile},'input_evidence':{},'limitations':[],'errors':[]}
        self.workflow.adapters['planning_draft'] = lambda _payload: WireResponse(json.dumps({
            'contract_version':'novelty-handoff','request_id':self.rid,
            'producer':{'agent_id':'novelty','schema_version':'1.0.0'},'status':'partial','result':draft_result}).encode())
        def p07(payload):
            calls.append(copy.deepcopy(payload))
            return WireResponse(json.dumps(native_p07_review(self.rid, payload['input']['product'])).encode())
        self.workflow.adapters['sales_market_review'] = p07
        self.workflow.image = None
        result = self.run_flow()['envelope']
        self.assertNotEqual(result['status'], 'error', result['result']['errors'])
        self.assertEqual(calls[0]['input'], {
            'request_id': self.rid,
            'product': {'category': 'Outerwear', 'product_type': 'Outerwear', 'gender': 'unknown',
                        'season': 'winter', 'material': 'wool blend'},
            'product_evidence': {
                'category': {'kind': 'user_requested', 'source_ref': 'novelty.draft.product_profile.category', 'evidence_ids': []},
                'product_type': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.product_type', 'evidence_ids': []},
                'gender': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.gender', 'evidence_ids': []},
                'season': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.season', 'evidence_ids': []},
                'material': {'kind': 'evidence_observation', 'source_ref': 'novelty.draft.product_profile.material',
                             'evidence_ids': ['trend:e1']}}})
        self.contract.semantic(result, self.rid, self.store)

    def test_p07_receives_the_proposed_material_with_its_proposal_kind(self):
        from vogue_orchestrator.workflow_contract import p07_review_input
        profile = {'category':{'value':'가디건','kind':'user_requested','evidence_ids':[]},
                   'material':{'value':'울 혼방 니트','kind':'proposal','evidence_ids':[]},
                   'season':{'value':'2026FW','kind':'proposal','evidence_ids':[]},
                   **{key:{'value':None,'kind':'unknown','evidence_ids':[]} for key in ('product_type','gender')}}
        draft = {'evidence_ids': [], 'draft': {'category': '가디건', 'product_profile': profile}}
        self.assertEqual(p07_review_input(draft, 'req-1'), {
            'request_id': 'req-1',
            'product': {'category': '가디건', 'product_type': '가디건', 'gender': 'unknown',
                        'season': '2026FW', 'material': '울 혼방 니트'},
            'product_evidence': {
                'category': {'kind': 'user_requested', 'source_ref': 'novelty.draft.product_profile.category', 'evidence_ids': []},
                'product_type': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.product_type', 'evidence_ids': []},
                'gender': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.gender', 'evidence_ids': []},
                'season': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.season', 'evidence_ids': []},
                'material': {'kind': 'proposal', 'source_ref': 'novelty.draft.product_profile.material', 'evidence_ids': []}}})

    def test_p06_trend_basis_and_the_p07_consumer_projection_pass_every_check(self):
        """A Draft with draft.trend_basis (P06 rule, 2026-11-17) and P07 main c662d4d's response run end to end."""
        from vogue_orchestrator.planning_sheet import build_sheet, draft_trend_citations, focus_stock, focus_trend
        # Two Trend styles and a Draft that cites neither: without trend_basis the focus would be the first style,
        # so the focus below is the basis's doing.
        first = self.sample['input']['trend_response']['raw_response']['result']['trends'][0]['style']['id']
        style = add_second_trend_style(self.sample)
        profile = {'category': {'value': 'Outerwear', 'kind': 'user_requested', 'evidence_ids': []},
                   'material': {'value': 'wool', 'kind': 'proposal', 'evidence_ids': []},
                   **{key: {'value': None, 'kind': 'unknown', 'evidence_ids': []} for key in ('product_type', 'gender', 'season')}}
        draft = {'title': '검증용 Draft', 'category': 'Outerwear', 'product_profile': profile, 'limitations': [],
                 'trend_basis': {'source_style_id': style['id'], 'name': style['name'],
                                 'selection_rule': 'most_evidence_linked_design_attributes/1',
                                 'evidence_ids': [], 'design_attributes': [], 'candidates_considered': 2}}
        result = {'schema_version': '1.0.0', 'request_id': self.rid, 'status': 'ok', 'draft': draft, 'evidence_ids': [],
                  'input_evidence': {}, 'limitations': [], 'errors': []}
        sent = []

        def p06(_payload):
            self.trace.append('planning_draft')
            return WireResponse(json.dumps({'contract_version': 'novelty-handoff', 'request_id': self.rid,
                                            'producer': {'agent_id': 'novelty', 'schema_version': '1.0.0'},
                                            'status': 'ok', 'result': result}, ensure_ascii=False).encode())

        def p07(payload):
            self.trace.append('sales_market_review')
            sent.append(copy.deepcopy(payload['input']['product']))
            review = consumer_projection_p07_review(self.rid, payload['input']['product'])
            return WireResponse(json.dumps(review, ensure_ascii=False).encode())

        self.workflow.adapters['planning_draft'] = p06
        self.workflow.adapters['sales_market_review'] = p07
        envelope = self.run_flow()['envelope']
        self.assertEqual(envelope['status'], 'ok', envelope['result']['errors'])
        self.assertEqual(sent, [{'category': 'Outerwear', 'product_type': 'Outerwear', 'gender': 'unknown',
                              'season': '미정', 'material': 'wool'}])
        self.contract.schema(envelope, 'integrated-result.schema.json')
        self.contract.semantic(envelope, self.rid, self.store)
        body = envelope['result']
        self.assertEqual([trend['style']['id'] for trend in body['sources']['trend']['raw_response']['result']['trends']],
                         [first, style['id']])
        self.assertEqual(draft_trend_citations(body), {})
        self.assertEqual(focus_trend(body)['style']['id'], style['id'])
        self.assertEqual(focus_stock(body)[1]['style_id'], style['id'])
        sheet = build_sheet(envelope)
        self.assertEqual(sheet.trend.name, '합성 블라우스')
        self.assertEqual(sheet.stock.style_id, style['id'])
        self.assertEqual(sheet.price_rows, (('제안 판매가', '66,000원'), ('업계 평균 마진 참고치', '56.88%')))
        self.assertIn('[마진 참고] 미국 의류 업계 평균 매출총이익률 벤치마크이며 이 상품의 실제 마진이 아닙니다.',
                      envelope['result']['integration']['limitations'])

    def test_user_category_season_and_partial_peer_results_are_preserved(self):
        scenario = json.loads((FIXTURES/'p06-p07-partial-handoff.json').read_bytes())
        request = self.sample['input']
        query = scenario['query']
        request['user_input']['text'] = query
        request['planning_request']['query'] = query
        request['planning_request']['context'] = copy.deepcopy(scenario['planning_context'])
        p06_raw = scenario['p06_response']
        p07_raw = scenario['p07_response']
        calls = {}

        def p06(payload):
            calls['p06'] = copy.deepcopy(payload)
            return WireResponse(json.dumps(p06_raw, ensure_ascii=False).encode())

        def p07(payload):
            calls['p07'] = copy.deepcopy(payload)
            return WireResponse(json.dumps(p07_raw, ensure_ascii=False).encode())

        self.workflow.adapters['planning_draft'] = p06
        self.workflow.adapters['sales_market_review'] = p07
        self.workflow.image = None
        envelope = self.run_flow()['envelope']
        body = envelope['result']

        self.assertEqual(calls['p06']['input']['planning_request'], request['planning_request'])
        self.assertEqual(calls['p06']['input']['planning_request']['context'], scenario['planning_context'])
        # P06's proposed material ('니트', kind proposal) is forwarded too; product_type and gender are always sent,
        # filled with labelled 'proposal' placeholders when the profile has no value.
        self.assertEqual(calls['p07']['input']['product'], {'category': '니트웨어', 'product_type': '니트웨어',
                                                            'gender': 'unknown', 'season': '2027SS', 'material': '니트'})
        self.assertEqual(calls['p07']['input']['product_evidence']['material']['kind'], 'proposal')
        self.assertEqual(body['status'], 'partial')
        self.assertEqual(body['sources']['planning_draft']['raw_response'], p06_raw)
        self.assertEqual(body['sources']['sales_market_review']['raw_response'], p07_raw)
        self.assertEqual(body['sources']['sales_market_review']['raw_response']['result']['missing_data'], p07_raw['result']['missing_data'])
        self.assertIn('CUSTOMER_AGE_RESEARCH_FAILED', [error['code'] for error in body['errors']])
        self.assertEqual(body['sources']['planning_draft']['raw_response']['result']['draft']['field_origins'],
                         {'category': 'user', 'season': 'user'})
        for stage, response in (('planning_draft', p06_raw), ('sales_market_review', p07_raw)):
            retained = body['sources'][stage]
            self.assertEqual(retained['content_sha256'], digest(response))
            self.assertEqual(json.loads(self.store.verify(retained['raw_artifact'])), response)
            trace = next(item for item in body['stages'] if item['stage'] == stage)
            self.assertEqual((trace['status'], trace['request_id']), (response['status'], self.rid))
            sent = json.loads(self.store.verify(retained['call_request_artifact']))
            self.assertEqual(sent, calls['p06' if stage == 'planning_draft' else 'p07'])

        downgraded = copy.deepcopy(envelope)
        downgraded['status'] = downgraded['result']['status'] = 'no_results'
        with self.assertRaisesRegex(ValueError, 'AGGREGATE_STATUS_MISMATCH'):
            self.contract.semantic(downgraded, self.rid, self.store)

    def test_partial_agent_error_is_preserved_and_timing_logs_are_safe(self):
        from vogue_orchestrator.workflow import LOGGER

        def mutate(stage, value):
            if stage == 'sales_market_review':
                value['status'] = value['result']['status'] = 'partial'
                value['result']['errors'] = [{
                    'code': 'CUSTOMER_AGE_RESEARCH_FAILED',
                    'message': 'Synthetic source aggregate failure.',
                    'retryable': False,
                }]
            return value

        self.mutate = mutate
        original = self.workflow.adapters['planning_draft']

        def measured(payload):
            response = original(payload)
            return WireResponse(response.body, response.status, response.media_type, response.truncated, 12.5)

        self.workflow.adapters['planning_draft'] = measured
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        LOGGER.addHandler(handler)
        self.addCleanup(LOGGER.removeHandler, handler)
        previous_level = LOGGER.level
        LOGGER.setLevel(logging.INFO)
        self.addCleanup(LOGGER.setLevel, previous_level)
        try:
            result = self.run_flow()
        finally:
            LOGGER.setLevel(previous_level)

        body = result['envelope']['result']
        self.assertEqual(result['envelope']['status'], 'partial')
        self.assertEqual(body['sources']['sales_market_review']['raw_response']['result']['errors'], [
            {'code': 'CUSTOMER_AGE_RESEARCH_FAILED', 'message': 'Synthetic source aggregate failure.', 'retryable': False}
        ])
        self.assertIn({
            'code': 'CUSTOMER_AGE_RESEARCH_FAILED', 'message': 'Synthetic source aggregate failure.',
            'stage': 'sales_market_review', 'fatal': False, 'retryable': False,
        }, body['errors'])

        events = [json.loads(record.getMessage()) for record in records]
        call = next(event for event in events if event['event'] == 'workflow_agent_call' and event['stage'] == 'planning_draft')
        self.assertEqual((call['request_id'], call['agent_id'], call['status'], call['upstream_http_ms']),
                         (self.rid, 'novelty', 'ok', 12.5))
        self.assertGreaterEqual(call['p08_call_overhead_ms'], 0)
        p07 = next(event for event in events if event['event'] == 'workflow_agent_call' and event['stage'] == 'sales_market_review')
        self.assertEqual((p07['status'], p07['retryable'], p07['fatal']), ('partial', False, False))
        completed = next(event for event in events if event['event'] == 'workflow_stage_completed' and event['stage'] == 'sales_market_review')
        self.assertGreaterEqual(completed['duration_ms'], 0)
        run_event = next(event for event in events if event['event'] == 'workflow_completed')
        self.assertEqual(run_event['status'], 'partial')
        self.assertGreaterEqual(run_event['duration_ms'], completed['duration_ms'])
        self.assertNotIn('payload', call)
        self.assertNotIn('raw_response', call)

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

    def test_image_failure_keeps_the_provider_code_in_the_error_message(self):
        class ProviderFailure(RuntimeError):
            code = 'IMAGE_API_HTTP_403'
        def failing(payload):
            self.trace.append('final_image')
            raise ProviderFailure()
        self.workflow.image = failing
        first = self.run_flow()
        result = first['envelope']['result']
        self.assertEqual(result['final_image']['reason'], 'IMAGE_GENERATION_FAILED')
        self.assertNotIn('provider_error', result['final_image'])
        self.assertEqual(result['errors'][-1]['code'], 'IMAGE_GENERATION_FAILED')
        self.assertIn('Provider error: IMAGE_API_HTTP_403.', result['errors'][-1]['message'])
        self.assertEqual(self.run_flow(), first)
        self.assertEqual(self.trace.count('final_image'), 1)

    def test_text_and_image_reference_reaches_full_draft(self):
        image = self.store.upload(self.image_data, 'image/png', self.rid)
        self.sample['input']['user_input']['images'] = [image]
        result = self.run_flow()
        self.assertEqual(result['envelope']['status'], 'ok', result)
        draft = result['envelope']['result']['sources']['planning_draft']['raw_response']['result']
        call_request = json.loads(self.store.verify(result['envelope']['result']['sources']['planning_draft']['call_request_artifact']))
        self.assertEqual(call_request['input']['planning_request'], self.sample['input']['planning_request'])
        self.assertNotIn('user_input', call_request['input'])
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
        self.assertEqual(image['artifact']['uri'], 'artifact://orchestrator/blobs/' + image['artifact']['sha256'])
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
        payload = dict(contract_version='trend-handoff', request_id=self.rid, input=trend_query)
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
        return dict(artifact_id='p07-review-' + sha[:16], uri='artifact://research/reviews/%s.json' % sha, sha256=sha,
                    media_type='application/json', size_bytes=len(data), immutable=True)

    def review_carries(self, reference):
        def mutate(stage, value):
            if stage == 'sales_market_review':
                value['result']['artifacts'] = [reference]
            return value
        self.mutate = mutate

    def peer_root(self, reference, data):
        root = Path(self.tmp.name) / 'peer'
        (root / 'research' / 'reviews').mkdir(parents=True)
        (root / 'research' / 'reviews' / (reference['sha256'] + '.json')).write_bytes(data)
        return root

    def test_p07_artifact_field_is_rejected_because_main_contract_has_no_artifacts(self):
        self.review_carries(self.p07_reference(b'{"review":"synthetic"}'))
        result = self.run_flow()
        body = result['envelope']['result']
        self.assertEqual((result['http_status'], body['errors'][-1]['code']), (502, 'CONTRACT_VIOLATION'))
        self.assertNotIn('final_image', self.trace)

    def test_run_level_limitations_are_appended_to_the_result(self):
        note = 'workflow-contract not frozen; development run'
        self.workflow = Workflow(**{k: self.workflow.adapters[s] for k, s in (('trend', 'trend'), ('stock', 'stock'),
                                    ('draft', 'planning_draft'), ('review', 'sales_market_review'))},
                                 image=self.workflow.image, store=self.store, contract=self.contract, limitations=[note],
                                 draft_mode=self.draft_mode)
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

    # novelty input projection (upstream_projection): novelty receives the projection, the run keeps the raw.

    def use_upstream_projection_fixture(self):
        """Trend/Stock answers in which every trend projection rule has something to drop (similar products,
        evidence url and title), and a stock image carrying base64 data (no url) next to a null stock slot."""
        upstream = json.loads((FIXTURES/'upstream-projection.json').read_bytes())
        trend_hash = digest(upstream['trend_response'])

        def mutate(stage, value):
            if stage in {'trend', 'stock'}:
                return copy.deepcopy(upstream[stage + '_response'])
            if stage == 'planning_draft':
                # draft-response-ok.json cites the base sample's Trend response by hash; cite this run's instead.
                value['result']['draft']['claims'][0]['evidence_refs'][0]['source_response_sha256'] = trend_hash
                value['result']['draft_identity']['content_sha256'] = digest(value['result']['draft'])
            return value
        self.mutate = mutate
        return upstream['trend_response']['result'], upstream['stock_response']['result']

    def rebuild(self, **options):
        """The same fake producers in a Workflow with extra options."""
        adapters = self.workflow.adapters
        self.workflow = Workflow(trend=adapters['trend'], stock=adapters['stock'], draft=adapters['planning_draft'],
                                 review=adapters['sales_market_review'], image=self.workflow.image, store=self.store,
                                 contract=self.contract, draft_mode=self.draft_mode,
                                 artifact_reader=self.artifact_reader, **options)

    def capture_p06(self):
        sent = []
        original = self.workflow.adapters['planning_draft']

        def p06(payload):
            sent.append(copy.deepcopy(payload))
            return original(payload)
        self.workflow.adapters['planning_draft'] = p06
        return sent

    def test_p06_receives_the_projection_and_the_run_keeps_the_raw(self):
        from vogue_orchestrator.upstream_projection import novelty_inputs
        from vogue_orchestrator.workflow import LOGGER
        trend_raw, stock_raw = self.use_upstream_projection_fixture()
        sent = self.capture_p06()
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(json.loads(record.getMessage()))

        handler = Capture()
        LOGGER.addHandler(handler)
        self.addCleanup(LOGGER.removeHandler, handler)
        self.addCleanup(LOGGER.setLevel, LOGGER.level)
        LOGGER.setLevel(logging.INFO)
        envelope = self.run_flow()['envelope']
        body = envelope['result']
        self.assertEqual(envelope['status'], 'ok', body['errors'])
        self.assertEqual(self.trace, ['trend', 'stock', 'planning_draft', 'sales_market_review', 'final_image'])
        # The run keeps the raw results, image data included; P06 received the projection.
        self.assertEqual(body['sources']['trend']['raw_response']['result'], trend_raw)
        self.assertEqual(body['sources']['stock']['raw_response']['result'], stock_raw)
        self.assertIn('data', body['sources']['stock']['raw_response']['result']['products'][0]['image'])
        trend_view, stock_view = novelty_inputs(trend_raw, stock_raw)
        self.assertEqual((sent[0]['input']['trend_result'], sent[0]['input']['inventory_product_result']), (trend_view, stock_view))
        # The product's photo reaches P06 once, for the outfit image with the stock item.
        self.assertEqual(sent[0]['input']['inventory_product_result']['products'], [stock_raw['products'][0], None])
        self.assertEqual(json.loads(self.store.verify(body['sources']['planning_draft']['call_request_artifact'])), sent[0])
        raw_request = dict(sent[0], input=dict(sent[0]['input'], trend_result=trend_raw, inventory_product_result=stock_raw))
        self.assertLess(len(canonical(sent[0])), len(canonical(raw_request)))
        self.contract.schema(envelope, 'integrated-result.schema.json')
        self.contract.semantic(envelope, self.rid, self.store)  # integrated(): P06 saw the projection
        # The call log carries the size of the bytes sent (no payload).
        call = next(e for e in records if e['event'] == 'workflow_agent_call' and e['stage'] == 'planning_draft')
        self.assertEqual(call['request_bytes'], len(canonical(sent[0])))
        self.assertTrue(all('request_bytes' in e for e in records if e['event'] == 'workflow_agent_call'))

    def test_stock_still_receives_the_raw_trend_result(self):
        # Decision B4: only P06 gets the projection. The stock trace and integrated() hash the stored raw result
        # rather than the bytes sent, so a regression here would pass both; pin what stock actually receives.
        from vogue_orchestrator.upstream_projection import trend_view
        trend_raw, stock_raw = self.use_upstream_projection_fixture()
        sent = []
        original = self.workflow.adapters['stock']

        def stock(payload):
            sent.append(copy.deepcopy(payload))
            return original(payload)
        self.workflow.adapters['stock'] = stock
        envelope = self.run_flow()['envelope']
        self.assertEqual(envelope['status'], 'ok', envelope['result']['errors'])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['input'], trend_raw)
        # Meaningful only because P06's view of the same result differs (similar_products, evidence url/title).
        self.assertNotEqual(sent[0]['input'], trend_view(trend_raw))

    def test_a_changed_projection_stops_before_p06(self):
        from unittest.mock import patch
        from vogue_orchestrator import workflow as workflow_module
        from vogue_orchestrator.upstream_projection import novelty_inputs
        from vogue_orchestrator.workflow_contract import ContractViolation, require_novelty_inputs

        def drop_quotes(trend, stock):  # not the projection the check recomputes
            for row in trend['evidence']:
                row.pop('quote')

        def drop_slot(trend, stock):
            stock['products'].pop()

        for change, code in ((drop_quotes, 'P06_TREND_RESULT_CHANGED'), (drop_slot, 'P06_STOCK_RESULT_CHANGED')):
            with self.subTest(code=code):
                self.setUp()
                self.use_upstream_projection_fixture()
                raised = []

                def unregistered(trend_result, stock_result):
                    trend, stock = novelty_inputs(trend_result, stock_result)
                    change(trend, stock)
                    return trend, stock

                def checked(*args):
                    try:
                        return require_novelty_inputs(*args)
                    except ContractViolation as exc:
                        raised.append(str(exc))
                        raise

                with patch.object(workflow_module, 'novelty_inputs', unregistered), \
                        patch.object(workflow_module, 'require_novelty_inputs', checked):
                    result = self.run_flow()
                body = result['envelope']['result']
                self.assertEqual(raised, [code])
                self.assertEqual(self.trace, ['trend', 'stock'])  # the P06 adapter was called 0 times
                self.assertEqual((result['http_status'], body['errors'][-1]['stage'], body['errors'][-1]['code']),
                                 (502, 'planning_draft', 'CONTRACT_VIOLATION'))
                self.assertIsNone(body['sources']['planning_draft'])
                with self.store.db() as db:  # nothing was recorded for a P06 call
                    rows = db.execute("SELECT value FROM checkpoints WHERE key LIKE '%:request'").fetchall()
                self.assertEqual(sorted(json.loads(value)['stage'] for (value,) in rows), ['stock', 'trend'])

    def test_p06_binding_accepts_the_projection_only(self):
        from vogue_orchestrator.upstream_projection import novelty_inputs
        from vogue_orchestrator.workflow_contract import ContractViolation
        trend_raw, stock_raw = self.use_upstream_projection_fixture()
        envelope = self.run_flow()['envelope']
        self.assertEqual(envelope['status'], 'ok', envelope['result']['errors'])
        draft = envelope['result']['sources']['planning_draft']
        sent = json.loads(self.store.verify(draft['call_request_artifact']))
        trend_view, stock_view = novelty_inputs(trend_raw, stock_raw)

        def as_if_p06_received(**fields):
            """The stored run, with the request P06 received replaced (None = field omitted)."""
            call = copy.deepcopy(sent)
            for key, value in fields.items():
                if value is None:
                    call['input'].pop(key)
                else:
                    call['input'][key] = value
            value = copy.deepcopy(envelope)
            value['result']['sources']['planning_draft']['call_request_artifact'] = self.store.put(canonical(call))
            return value

        def changed(value, edit):
            value = copy.deepcopy(value)
            edit(value)
            return value

        accepted = {
            'projection (as sent)': {},
            'projection (recomputed)': dict(trend_result=trend_view, inventory_product_result=stock_view),
        }
        rejected = {
            'raw trend': (dict(trend_result=trend_raw), 'P06_TREND_RESULT_CHANGED'),
            'trend omitted': (dict(trend_result=None), 'P06_TREND_RESULT_CHANGED'),
            'stock omitted': (dict(inventory_product_result=None), 'P06_STOCK_RESULT_CHANGED'),
            'trend tampered': (dict(trend_result=changed(trend_view, lambda t: t['evidence'][0].update(quote='바뀐 문장'))),
                               'P06_TREND_RESULT_CHANGED'),
            'stock tampered': (dict(inventory_product_result=changed(stock_view, lambda s: s['products'][0]['inventory'].update(quantity=8))),
                               'P06_STOCK_RESULT_CHANGED'),
            'trend evidence emptied': (dict(trend_result=changed(trend_view, lambda t: t.update(evidence=[]))),
                                       'P06_TREND_RESULT_CHANGED'),
            'trend other projection (quote dropped)': (
                dict(trend_result=changed(trend_view, lambda t: [row.pop('quote') for row in t['evidence']])),
                'P06_TREND_RESULT_CHANGED'),
            'stock other projection (image data dropped, image kept)': (
                dict(inventory_product_result=changed(stock_raw, lambda s: s['products'][0]['image'].pop('data'))),
                'P06_STOCK_RESULT_CHANGED'),
        }
        for name, fields in accepted.items():
            with self.subTest(accepted=name):
                self.contract.integrated(as_if_p06_received(**fields), self.store)
        for name, (fields, code) in rejected.items():
            with self.subTest(rejected=name), self.assertRaisesRegex(ContractViolation, code):
                self.contract.integrated(as_if_p06_received(**fields), self.store)

    def test_an_image_retry_of_a_projected_run_passes_the_binding(self):
        self.use_upstream_projection_fixture()
        self.fail_image = True
        first = self.run_flow()
        self.assertEqual(first['envelope']['result']['final_image']['status'], 'failed')
        self.fail_image = False
        retried = self.workflow.retry_image(self.rid, 'retry-projection')
        self.assertEqual((retried['envelope']['status'], retried['envelope']['result']['final_image']['status']), ('ok', 'generated'))
        self.assertEqual(self.trace.count('planning_draft'), 1)
        self.contract.semantic(retried['envelope'], self.rid, self.store)

    def test_a_run_that_died_between_p06_and_initial_resumes_without_calling_p06(self):
        from unittest.mock import patch
        from vogue_orchestrator import workflow as workflow_module

        class Crash(BaseException):  # stands in for the process dying; run() only catches Exception
            pass

        def dying(draft, request_id):  # building the P07 request: after the P06 call, before 'initial'
            raise Crash()

        keys = []
        key = Workflow._key

        def recording_key(workflow, rid, stage, payload, attempt='initial'):
            value = key(workflow, rid, stage, payload, attempt)
            if stage == 'planning_draft':
                keys.append(value)
            return value

        self.use_upstream_projection_fixture()
        with patch.object(Workflow, '_key', recording_key):
            with patch.object(workflow_module, 'p07_review_input', dying), self.assertRaises(Crash):
                self.run_flow()
            self.assertEqual(self.trace, ['trend', 'stock', 'planning_draft'])
            self.assertIsNone(self.store.run(self.rid))  # no 'initial'
            self.assertIsNotNone(self.store.run(self.rid, 'progress'))
            self.workflow = self.build()  # a new process on the same store
            result = self.run_flow()
        envelope = result['envelope']
        self.assertEqual(envelope['status'], 'ok', envelope['result']['errors'])
        # Rebuilt from the checkpointed raw results, the projection gives the same P06 key: no second P06 call.
        self.assertEqual(self.trace, ['trend', 'stock', 'planning_draft', 'sales_market_review', 'final_image'])
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.contract.semantic(envelope, self.rid, self.store)  # integrated() passes on the resumed run


def _png(colour, size=(48, 72)):
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return buffer.getvalue()


@unittest.skipUnless(importlib.util.find_spec('jsonschema') and importlib.util.find_spec('PIL'), 'Install the workflow extra.')
class DraftVisualsWorkflowTests(unittest.TestCase):
    """workflow-contract 0.2.0: P06 returns the product and outfit images; stage 06 composes the planning sheet."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p08-visuals-')
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(os.path.realpath(self.tmp.name))
        self.contract = WorkflowContract()
        self.sample = json.loads((FIXTURES/'draft-request-text-ok.json').read_bytes())
        self.rid = self.sample['request_id']
        self.sent = {}
        self.images = {'product': _png((180, 40, 60)), 'outfit': _png((40, 60, 180))}
        self.outfit_status = 'generated'
        self.blobs = {}

    def visual(self, key):
        data = self.images[key]
        sha = digest_bytes(data)
        self.blobs[sha] = data
        artifact = dict(artifact_id='p06:image:' + sha, uri=f'artifact://novelty/images/{sha}.png', sha256=sha,
                        media_type='image/png', size_bytes=len(data), immutable=True)
        if key == 'outfit' and self.outfit_status == 'failed':
            return dict(status='failed', artifact=None, provider_calls=1, model='fixture', prompt_version='fixture-visual/1',
                        reason='IMAGE_API_HTTP_400:moderation_blocked')
        return dict(status='generated', artifact=artifact, provider_calls=1, model='fixture', prompt_version='fixture-visual/1', reason=None)

    def call(self, stage, payload):
        self.sent[stage] = payload
        if stage in {'trend', 'stock'}:
            value = copy.deepcopy(self.sample['input'][stage + '_response']['raw_response'])
        elif stage == 'planning_draft':
            value = json.loads((FIXTURES/'draft-response-ok.json').read_bytes())
            body = value['result']
            evidence = payload['input']['trend_result']['evidence'][0]['evidence_id']
            body['draft']['concept'] = dict(engine='rule_based', product_definition=dict(value='합성 셔츠 콘셉트', kind='proposal', evidence_ids=[evidence]),
                                            opportunity=None, target_customer=None, attributes=[], differentiation=[], risks=[],
                                            questions_for_validation=['색상은 MD 확인 필요'])
            body['draft']['visuals'] = {key: self.visual(key) for key in ('product', 'outfit')}
            body['image_generation_calls'] = 2
            body['draft_identity']['content_sha256'] = digest(body['draft'])
            value['contract_version'] = 'novelty-handoff'
            value['producer']['schema_version'] = '1.0.0'
            body['schema_version'] = '1.0.0'
            body['input_evidence'] = body.get('input_evidence', {})
            body.pop('source_request', None)
            body.pop('source_request_sha256', None)
            if self.outfit_status != 'generated':
                value['status'] = body['status'] = 'partial'
            self._last_draft_response = copy.deepcopy(value)
        else:
            value = native_p07_review(self.rid, payload['input']['product'])
        return WireResponse(json.dumps(value, ensure_ascii=False).encode())

    def reader(self, node):
        return self.blobs[node['sha256']]

    def run_flow(self, reader=True, trend_photos=None, image=None, swot=None):
        from unittest.mock import patch
        from vogue_orchestrator.workflow_http import planning_sheet_image
        workflow = Workflow(trend=lambda p: self.call('trend', p), stock=lambda p: self.call('stock', p),
                            draft=lambda p: self.call('planning_draft', p), review=lambda p: self.call('sales_market_review', p),
                            image=image or planning_sheet_image, store=self.store, contract=self.contract,
                            artifact_reader=self.reader if reader else None, trend_photos=trend_photos, swot=swot)
        request = self.sample['input']
        with patch.dict(os.environ, {'VOGUE_WORKFLOW_IMAGES_ENABLED': '1'}):
            return workflow.run(request_id=self.rid, user_input=request['user_input'], planning_request=request['planning_request'],
                                trend_query=dict(request_id=self.rid, original_query=request['user_input']['text']))

    def test_p06_visuals_are_imported_and_stage_06_is_the_composed_sheet(self):
        from PIL import Image
        import io
        from vogue_orchestrator.planning_sheet import build_sheet
        result = self.run_flow()
        envelope = result['envelope']
        body = envelope['result']
        self.assertEqual(self.sent['planning_draft']['contract_version'], 'novelty-handoff')
        self.assertEqual(set(self.sent['planning_draft']['input']), {'planning_request', 'trend_result', 'inventory_product_result'})
        self.assertEqual((envelope['status'], [s['status'] for s in body['stages']]), ('ok', ['ok'] * 6), body['errors'])
        self.contract.schema(envelope, 'integrated-result.schema.json')
        self.contract.semantic(envelope, self.rid, self.store)
        for key, visual in body['sources']['planning_draft']['raw_response']['result']['draft']['visuals'].items():
            self.assertEqual(self.store.verify(visual['artifact']), self.images[key])  # imported through the reader
        image = body['final_image']
        self.assertEqual((image['status'], image['provider_calls']), ('generated', 1))
        with Image.open(io.BytesIO(self.store.verify(image['artifact']))) as sheet:
            self.assertEqual((sheet.format, sheet.width), ('PNG', 3200))
        visuals = build_sheet(envelope).visual.draft_visuals
        self.assertEqual([(v.key, v.status) for v in visuals], [('product', 'generated'), ('outfit', 'generated')])

    def test_a_failed_outfit_keeps_the_run_partial_and_still_composes_the_sheet(self):
        self.outfit_status = 'failed'
        result = self.run_flow()
        body = result['envelope']['result']
        self.assertEqual((result['envelope']['status'], body['final_image']['status']), ('partial', 'generated'))
        self.assertEqual(body['stages'][2]['status'], 'partial')

    def test_stage_06_hands_the_stored_trend_photos_to_the_sheet(self):
        from vogue_orchestrator.workflow_http import planning_sheet_image
        seen, hook_calls = [], []

        def photos(rid, envelope):
            hook_calls.append((rid, envelope['result']['request_id']))
            return [(b'photo-bytes', 'https://img.vogue.co.kr/a.jpg')]

        def image(payload):
            seen.append(payload['trend_photos'])
            return planning_sheet_image(dict(payload, trend_photos=()))

        result = self.run_flow(trend_photos=photos, image=image)
        self.assertEqual(result['envelope']['status'], 'ok', result['envelope']['result']['errors'])
        self.assertEqual(hook_calls, [(self.rid, self.rid)])
        self.assertEqual(seen, [[(b'photo-bytes', 'https://img.vogue.co.kr/a.jpg')]])

    def test_stage_06_hands_the_swot_draft_to_the_sheet(self):
        from vogue_orchestrator.workflow_http import planning_sheet_image
        seen = []

        def image(payload):
            seen.append(payload['swot'])
            return planning_sheet_image(dict(payload, swot=None))

        record = {'swot': {'strengths': [], 'weaknesses': [], 'opportunities': [], 'threats': []}}
        result = self.run_flow(image=image, swot=lambda rid, envelope: record)
        self.assertEqual((result['envelope']['status'], seen), ('ok', [record]))

    def test_a_failing_swot_hook_leaves_the_sheet_without_swot(self):
        def broken(rid, envelope):
            raise RuntimeError('model down')

        result = self.run_flow(swot=broken)
        self.assertEqual((result['envelope']['status'], result['envelope']['result']['final_image']['status']), ('ok', 'generated'))

    def test_a_failing_photo_hook_leaves_the_sheet_without_photos(self):
        def photos(rid, envelope):
            raise OSError('network down')

        result = self.run_flow(trend_photos=photos)
        body = result['envelope']['result']
        self.assertEqual((result['envelope']['status'], body['final_image']['status']), ('ok', 'generated'))

    def test_a_crash_while_waiting_for_swot_does_not_block_the_retried_sheet(self):
        class Crash(BaseException):  # stands in for the process dying mid-call
            pass

        def dying(rid, envelope):
            raise Crash()

        with self.assertRaises(Crash):
            self.run_flow(swot=dying)
        result = self.run_flow()  # the retry composes the sheet; no 'started' marker was left behind
        image = result['envelope']['result']['final_image']
        self.assertEqual((image['status'], image['reason']), ('generated', None))

    def test_p06_visuals_without_a_reader_stop_with_artifact_reader_required(self):
        result = self.run_flow(reader=False)
        self.assertEqual((result['http_status'], result['envelope']['result']['errors'][-1]['code']), (503, 'ARTIFACT_READER_REQUIRED'))


def digest_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()

if __name__ == '__main__':
    unittest.main()
