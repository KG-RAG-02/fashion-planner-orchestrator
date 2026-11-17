"""upstream_projection: the registered projections of the Trend/Stock results P08 sends to P06, and the
VOGUE_WORKFLOW_P06_INPUT_PROJECTION setting. Pure functions and configuration only; no network."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from _http_support import environment, real_tempdir
from vogue_orchestrator import workflow_http
from vogue_orchestrator.upstream_projection import (CURRENT_P06_INPUT_PROJECTION, P06_INPUT_PROJECTIONS, RAW,
                                                    p06_input_projection_valid, p06_inputs, p06_stock_views,
                                                    p06_trend_views)
from vogue_orchestrator.workflow_store import canonical, digest

FIXTURES = Path(__file__).parent / 'fixtures/workflow'
HAVE_JSONSCHEMA = bool(importlib.util.find_spec('jsonschema'))
AGENTS = {k: 'http://127.0.0.1:9' for k in ('VOGUE_TREND_AGENT_URL', 'VOGUE_STOCK_AGENT_URL',
                                            'VOGUE_NEW_PRODUCT_AGENT_URL', 'VOGUE_SALES_MARKET_AGENT_URL')}

# Every key ever registered. Stored runs that sent one must stay verifiable, so none is ever removed.
HISTORICAL_KEYS = ('p06-input/1',)
# The registered versions' output on upstream-projection.json (sha256 of canonical JSON). A registered version is
# never edited in place: a changed projection is a new key with new golden hashes, and these stay as they are.
FIXTURE_SHA256 = {'trend': '1f70fad1c24beaff6944178777181cba92c6a3e055482e13990f045f84b0e821',
                  'stock': '31fc88164eec64e39bccda4b308532121133ad8a5f2e1e1a87f3ab68d601cdc0'}
GOLDEN = {
    'p06-input/1': {'trend': '5a9daf39aa98a1a35200501d67e807abb66449a728e7e33e3cd152bf92adeb60',
                    'stock': '0f276a9dbd8a36c226f89ab2e1fdd1438a7e31c17e811c6f2e12686da0d15116'},
}


def upstream():
    """(trend_result, stock_result) of upstream-projection.json: every p06-input/1 rule has something to drop."""
    value = json.loads((FIXTURES / 'upstream-projection.json').read_bytes())
    return value['trend_response']['result'], value['stock_response']['result']


def base_sample():
    """(trend_result, stock_result) of the base workflow fixture (stock image null)."""
    value = json.loads((FIXTURES / 'draft-request-text-ok.json').read_bytes())['input']
    return value['trend_response']['raw_response']['result'], value['stock_response']['raw_response']['result']


class RegistryTests(unittest.TestCase):
    def test_every_historical_key_is_still_registered(self):
        self.assertLessEqual(set(HISTORICAL_KEYS), set(P06_INPUT_PROJECTIONS))
        self.assertIn(CURRENT_P06_INPUT_PROJECTION, P06_INPUT_PROJECTIONS)
        self.assertNotIn(RAW, P06_INPUT_PROJECTIONS)  # raw is a send mode, never a projection key

    def test_every_registered_version_has_golden_hashes(self):
        self.assertEqual(set(P06_INPUT_PROJECTIONS), set(GOLDEN))

    def test_the_registry_cannot_be_changed_at_run_time(self):
        with self.assertRaises(TypeError):
            P06_INPUT_PROJECTIONS['p06-input/1'] = P06_INPUT_PROJECTIONS['p06-input/1']
        with self.assertRaises(TypeError):
            del P06_INPUT_PROJECTIONS['p06-input/1']

    def test_registered_versions_still_produce_their_golden_output(self):
        trend, stock = upstream()
        self.assertEqual({'trend': digest(trend), 'stock': digest(stock)}, FIXTURE_SHA256,
                         'upstream-projection.json changed; the golden hashes pin the registered versions on it')
        for key, golden in GOLDEN.items():
            with self.subTest(version=key):
                trend_view, stock_view = p06_inputs(trend, stock, key)
                self.assertEqual({'trend': digest(trend_view), 'stock': digest(stock_view)}, golden)


class P06Input1Tests(unittest.TestCase):
    def test_drops_only_similar_products_evidence_url_and_title_and_image_without_url(self):
        trend, stock = upstream()
        trend_view, stock_view = p06_inputs(trend, stock, 'p06-input/1')
        expected_trend = copy.deepcopy(trend)
        for item in expected_trend['trends'][0]['items']:
            item.pop('similar_products', None)
        for row in expected_trend['evidence']:
            del row['url'], row['title']
        self.assertEqual(trend_view, expected_trend)
        self.assertEqual([row['quote'] for row in trend_view['evidence']], [row['quote'] for row in trend['evidence']])
        self.assertIn('data', stock['products'][0]['image'])
        self.assertEqual(stock_view, dict(stock, products=[dict(stock['products'][0], image=None), None]))

    def test_inputs_are_never_mutated_and_views_are_new_objects(self):
        trend, stock = upstream()
        before = canonical([trend, stock])
        for version in (RAW, *P06_INPUT_PROJECTIONS):
            with self.subTest(version=version):
                trend_view, stock_view = p06_inputs(trend, stock, version)
                trend_view['evidence'].clear()
                stock_view['products'][0]['inventory']['quantity'] = -1
                self.assertEqual(canonical([trend, stock]), before)
        p06_trend_views(trend)
        p06_stock_views(stock)
        self.assertEqual(canonical([trend, stock]), before)

    def test_deterministic(self):
        trend, stock = upstream()
        first = canonical(p06_inputs(trend, stock, 'p06-input/1'))
        self.assertEqual(canonical(p06_inputs(*upstream(), 'p06-input/1')), first)

    def test_lengths_order_and_null_slots_are_kept(self):
        image = dict(media_type='image/png', encoding='base64', data='AAAA', size_bytes=3, source='fixture')
        stock = dict(status='ok', products=[
            dict(style_id='a', image=image),
            None,
            dict(style_id='b', image=dict(image, url='https://fixture.invalid/b.png')),  # P06 reads image.url
            dict(style_id='c', image=None),
            dict(style_id='d'),
            'not-an-object',
        ])
        trend = dict(status='ok',
                     trends=[None, dict(items=[None, dict(id='i1', similar_products=[1]), dict(id='i2')]), dict(style={})],
                     evidence=[dict(evidence_id='e2', url=None, title='t', quote='q'), None, dict(evidence_id='e1', quote='r')])
        trend_view, stock_view = p06_inputs(trend, stock, 'p06-input/1')
        self.assertEqual(stock_view['products'], [dict(style_id='a', image=None), None, stock['products'][2],
                                                  dict(style_id='c', image=None), dict(style_id='d'), 'not-an-object'])
        self.assertEqual(trend_view['trends'], [None, dict(items=[None, dict(id='i1'), dict(id='i2')]), dict(style={})])
        self.assertEqual(trend_view['evidence'], [dict(evidence_id='e2', quote='q'), None, dict(evidence_id='e1', quote='r')])
        self.assertEqual(p06_inputs({}, {}, 'p06-input/1'), ({}, {}))  # nothing to drop is not an error

    def test_the_base_workflow_fixture_is_projected_too(self):
        # Its stock image is already null, but the Trend side still loses the similar products and the url/title.
        trend, stock = base_sample()
        trend_view, stock_view = p06_inputs(trend, stock, 'p06-input/1')
        self.assertEqual(stock_view, stock)
        self.assertNotEqual(trend_view, trend)

    @unittest.skipUnless(HAVE_JSONSCHEMA, 'jsonschema not installed (install the `workflow` extra)')
    def test_the_output_keeps_the_upstream_schema_versions_true(self):
        from vogue_orchestrator.workflow_contract import WorkflowContract
        contract = WorkflowContract()
        trend_schema = contract.validators['upstream/trend-result-1.1.schema.json']
        stock_schema = contract.validators['upstream/stock-result-1.0.schema.json']
        for name, (trend, stock) in (('upstream-projection.json', upstream()), ('draft-request-text-ok.json', base_sample())):
            for version in P06_INPUT_PROJECTIONS:
                with self.subTest(fixture=name, version=version):
                    self.assertTrue(trend_schema.is_valid(trend) and stock_schema.is_valid(stock))
                    trend_view, stock_view = p06_inputs(trend, stock, version)
                    self.assertTrue(trend_schema.is_valid(trend_view), list(trend_schema.iter_errors(trend_view))[:1])
                    self.assertTrue(stock_schema.is_valid(stock_view), list(stock_schema.iter_errors(stock_view))[:1])
                    self.assertEqual((trend_view['schema_version'], stock_view['schema_version']), ('1.1', '1.0'))
                    request = dict(contract_version='vogue-handoff/0.3', request_id=trend['request_id'], input=dict(
                        planning_request=dict(schema_version='1.0', request_id=trend['request_id']),
                        trend_result=trend_view, inventory_product_result=stock_view))
                    contract.schema(request, 'planning-draft-request.schema.json')


class ModeAndViewTests(unittest.TestCase):
    def test_raw_sends_equal_copies(self):
        trend, stock = upstream()
        trend_view, stock_view = p06_inputs(trend, stock, RAW)
        self.assertEqual((trend_view, stock_view), (trend, stock))
        self.assertIsNot(trend_view, trend)
        self.assertIsNot(stock_view, stock)

    def test_only_raw_and_registered_keys_are_valid(self):
        for value in (RAW, *P06_INPUT_PROJECTIONS):
            self.assertTrue(p06_input_projection_valid(value), value)
        for value in ('p06-input/2', 'full', '', None, 'RAW'):
            with self.subTest(value=value):
                self.assertFalse(p06_input_projection_valid(value))
                with self.assertRaisesRegex(ValueError, 'P06_INPUT_PROJECTION_INVALID'):
                    p06_inputs({}, {}, value)

    def test_views_are_the_raw_and_every_registered_version(self):
        trend, stock = upstream()
        trend_views, stock_views = p06_trend_views(trend), p06_stock_views(stock)
        self.assertIs(trend_views[0], trend)
        self.assertIs(stock_views[0], stock)
        self.assertEqual(trend_views[1:], [p06_inputs(trend, stock, key)[0] for key in P06_INPUT_PROJECTIONS])
        self.assertEqual(stock_views[1:], [p06_inputs(trend, stock, key)[1] for key in P06_INPUT_PROJECTIONS])


class SettingTests(unittest.TestCase):
    """VOGUE_WORKFLOW_P06_INPUT_PROJECTION: raw (rollout stage 1) or a registered key; default the current key."""

    def test_blank_means_the_current_projection_and_unknown_values_are_invalid(self):
        for blank in (None, '', '   ', '\t'):
            with self.subTest(blank=repr(blank)):
                env = {} if blank is None else {'VOGUE_WORKFLOW_P06_INPUT_PROJECTION': blank}
                self.assertEqual(workflow_http.p06_input_projection(env), 'p06-input/1')
        for value, expected in (('raw', 'raw'), (' RAW ', 'raw'), ('p06-input/1', 'p06-input/1'),
                                (' P06-Input/1 ', 'p06-input/1'), ('p06-input/2', None), ('full', None), ('none', None)):
            with self.subTest(value=value):
                self.assertEqual(workflow_http.p06_input_projection({'VOGUE_WORKFLOW_P06_INPUT_PROJECTION': value}), expected)

    @unittest.skipUnless(HAVE_JSONSCHEMA, 'jsonschema not installed (install the `workflow` extra)')
    def test_build_workflow_passes_the_setting_and_refuses_an_invalid_one(self):
        base = dict(VOGUE_WORKFLOW_ALLOW_PROPOSED='1', VOGUE_AGENT_HTTP_DEV_MODE='1',
                    VOGUE_WORKFLOW_STORE_DIR=str(real_tempdir(self) / 'store'),
                    VOGUE_AGENT_SERVICE_BEARER_TOKEN='synthetic-test-token', **AGENTS)
        for extra, expected in (({}, 'p06-input/1'), ({'VOGUE_WORKFLOW_P06_INPUT_PROJECTION': 'raw'}, 'raw'),
                                ({'VOGUE_WORKFLOW_P06_INPUT_PROJECTION': 'p06-input/1'}, 'p06-input/1')):
            with self.subTest(extra=extra), environment(**base, **extra):
                self.assertEqual(workflow_http.build_workflow().p06_input_projection, expected)
        with environment(**base, VOGUE_WORKFLOW_P06_INPUT_PROJECTION='p06-input/9'), \
                self.assertRaises(workflow_http.WorkflowUnavailable) as caught:
            workflow_http.build_workflow()
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('P06_INPUT_PROJECTION_INVALID', 409))
        self.assertIn('p06-input/1', str(caught.exception))

    @unittest.skipUnless(HAVE_JSONSCHEMA, 'jsonschema not installed (install the `workflow` extra)')
    def test_an_invalid_setting_is_a_409_before_any_work(self):
        handler = Mock()
        request = dict(contract_version='vogue-workflow/0.2', request_id='r-1', input=dict(user_input=dict(text='t', images=[])))
        with environment(VOGUE_WORKFLOW_ALLOW_PROPOSED='1', VOGUE_AGENT_HTTP_DEV_MODE='1',
                         VOGUE_WORKFLOW_P06_INPUT_PROJECTION='full', VOGUE_WORKFLOW_STORE_DIR=str(real_tempdir(self)),
                         VOGUE_AGENT_SERVICE_BEARER_TOKEN='synthetic-test-token', **AGENTS):
            workflow_http.dispatch(handler, request)
        status, body = handler._json.call_args.args
        self.assertEqual((status, body['error']['code'], body['error']['retryable']), (409, 'P06_INPUT_PROJECTION_INVALID', False))

    def test_the_workflow_refuses_an_unknown_projection(self):
        from vogue_orchestrator.workflow import Workflow
        from vogue_orchestrator.workflow_contract import ContractViolation
        with self.assertRaisesRegex(ContractViolation, 'P06_INPUT_PROJECTION_INVALID'):
            Workflow(trend=None, stock=None, draft=None, review=None, image=None, store=None, contract=None,
                     p06_input_projection='p06-input/9')


if __name__ == '__main__':
    unittest.main()
