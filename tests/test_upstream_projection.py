"""upstream_projection: the projection of the trend and inventory results the orchestrator sends novelty.
Pure functions only; no network."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from fashion_planner_orchestrator.upstream_projection import PROJECTION_ID, inventory_view, novelty_inputs, trend_view
from fashion_planner_orchestrator.workflow_store import canonical, digest

FIXTURES = Path(__file__).parent / 'fixtures/workflow'
HAVE_JSONSCHEMA = bool(importlib.util.find_spec('jsonschema'))

# The projection's output on upstream-projection.json (sha256 of canonical JSON). A changed projection changes
# these hashes, so the change is visible in review.
FIXTURE_SHA256 = {'trend': '1bbcd515db08f0b1be95fc1612b4ca74da74a6f21acdb585e18965a52c4a7997',
                  'inventory': '76db0a285f4a807db15431b859e755fe17bb128af90ae72fac128af1fee71902'}
# The fixture's photos belong to different products, so the inventory side stays whole.
GOLDEN = {'trend': '073d69aa050c2110040c864fdc5e1bc5356eb5c8255b39dea224ed82b62a2faf',
          'inventory': '76db0a285f4a807db15431b859e755fe17bb128af90ae72fac128af1fee71902'}


def upstream():
    """(trend_result, inventory_result) of upstream-projection.json: every trend rule has something to drop."""
    value = json.loads((FIXTURES / 'upstream-projection.json').read_bytes())
    return value['trend_response']['result'], value['stock_response']['result']


def base_sample():
    """(trend_result, inventory_result) of the base workflow fixture (inventory image null)."""
    value = json.loads((FIXTURES / 'draft-request-text-ok.json').read_bytes())['input']
    return value['trend_response']['raw_response']['result'], value['stock_response']['raw_response']['result']


class GoldenTests(unittest.TestCase):
    def test_the_projection_still_produces_its_golden_output(self):
        trend, inventory = upstream()
        self.assertEqual({'trend': digest(trend), 'inventory': digest(inventory)}, FIXTURE_SHA256,
                         'upstream-projection.json changed; the golden hashes pin the projection on it')
        trend_out, inventory_out = novelty_inputs(trend, inventory)
        self.assertEqual({'trend': digest(trend_out), 'inventory': digest(inventory_out)}, GOLDEN)

    def test_the_projection_has_one_name(self):
        self.assertEqual(PROJECTION_ID, 'novelty-input-projection')


class InventoryViewTests(unittest.TestCase):
    """Inventory side: each product's photo is sent once."""

    @staticmethod
    def inventory():
        def photo(tag):
            return dict(data=tag, media_type='image/jpeg', encoding='base64', size_bytes=3, source='s',
                        data_nature='sample')
        return {'schema_version': '1.0.0', 'products': [
            dict(style_id='0764600', image=photo('A')), None, dict(style_id='0838846', image=photo('B')),
            dict(style_id='0764600', image=photo('A')), dict(style_id='0838846', image=None),
            dict(style_id='0714927', image=dict(url='https://example.invalid/c.jpg', data='C')),
            dict(style_id='0714927', image=dict(url='https://example.invalid/c.jpg', data='C')),
            dict(image=photo('D')), dict(style_id='0838846', image=photo('B')), 'not-an-object',
        ], 'trend_results': [dict(style_id='s1', product_index=3)]}

    def test_each_products_photo_is_sent_once_in_its_first_entry(self):
        inventory = self.inventory()
        view = inventory_view(inventory)
        images = [product['image'] if isinstance(product, dict) else product for product in view['products']]
        self.assertEqual(images[0], inventory['products'][0]['image'])  # 0764600, first entry
        self.assertIsNone(images[3])                                     # 0764600 again
        self.assertEqual(images[2], inventory['products'][2]['image'])  # 0838846, first entry
        self.assertIsNone(images[8])                                     # 0838846 again
        self.assertEqual(images[5:7], [inventory['products'][5]['image']] * 2)  # url images are kept as they are
        self.assertEqual(images[7], inventory['products'][7]['image'])  # no style_id: nothing to deduplicate by
        self.assertIsNone(view['products'][1])
        self.assertEqual(view['products'][9], 'not-an-object')
        self.assertEqual(view['trend_results'], inventory['trend_results'])
        self.assertEqual(len(view['products']), len(inventory['products']))

    def test_the_base_workflow_fixture_is_kept_whole(self):
        _trend, inventory = base_sample()
        self.assertEqual(inventory_view(inventory), inventory)


class TrendViewTests(unittest.TestCase):
    def test_drops_only_similar_products_and_evidence_url_and_title(self):
        trend, _inventory = upstream()
        expected = copy.deepcopy(trend)
        for item in expected['trends'][0]['items']:
            item.pop('similar_products', None)
        for row in expected['evidence']:
            del row['url'], row['title']
        view = trend_view(trend)
        self.assertEqual(view, expected)
        self.assertEqual([row['quote'] for row in view['evidence']], [row['quote'] for row in trend['evidence']])

    def test_lengths_order_and_null_slots_are_kept(self):
        trend = dict(status='ok',
                     trends=[None, dict(items=[None, dict(id='i1', similar_products=[1]), dict(id='i2')]), dict(style={})],
                     evidence=[dict(evidence_id='e2', url=None, title='t', quote='q'), None, dict(evidence_id='e1', quote='r')])
        view = trend_view(trend)
        self.assertEqual(view['trends'], [None, dict(items=[None, dict(id='i1'), dict(id='i2')]), dict(style={})])
        self.assertEqual(view['evidence'], [dict(evidence_id='e2', quote='q'), None, dict(evidence_id='e1', quote='r')])
        self.assertEqual(novelty_inputs({}, {}), ({}, {}))  # nothing to drop is not an error

    def test_the_base_workflow_fixture_is_projected_too(self):
        # The trend side still loses the similar products and the url/title.
        trend, _inventory = base_sample()
        self.assertNotEqual(trend_view(trend), trend)


class PurityTests(unittest.TestCase):
    def test_inputs_are_never_mutated_and_views_are_new_objects(self):
        trend, inventory = upstream()
        before = canonical([trend, inventory])
        trend_out, inventory_out = novelty_inputs(trend, inventory)
        trend_out['evidence'].clear()
        inventory_out['products'][0]['inventory']['quantity'] = -1
        self.assertEqual(canonical([trend, inventory]), before)
        inventory_view(InventoryViewTests.inventory())

    def test_deterministic(self):
        first = canonical(novelty_inputs(*upstream()))
        self.assertEqual(canonical(novelty_inputs(*upstream())), first)

    @unittest.skipUnless(HAVE_JSONSCHEMA, 'jsonschema not installed (install the `workflow` extra)')
    def test_the_output_still_matches_the_upstream_schemas(self):
        from fashion_planner_orchestrator.workflow_contract import WorkflowContract
        contract = WorkflowContract()
        trend_schema = contract.validators['upstream/trend-result.schema.json']
        inventory_schema = contract.validators['upstream/inventory-result.schema.json']
        for name, (trend, inventory) in (('upstream-projection.json', upstream()), ('draft-request-text-ok.json', base_sample())):
            with self.subTest(fixture=name):
                self.assertTrue(trend_schema.is_valid(trend) and inventory_schema.is_valid(inventory))
                trend_out, inventory_out = novelty_inputs(trend, inventory)
                self.assertTrue(trend_schema.is_valid(trend_out), list(trend_schema.iter_errors(trend_out))[:1])
                self.assertTrue(inventory_schema.is_valid(inventory_out), list(inventory_schema.iter_errors(inventory_out))[:1])
                self.assertEqual((trend_out['schema_version'], inventory_out['schema_version']), ('1.0.0', '1.0.0'))
                request = dict(contract_version='novelty-handoff', request_id=trend['request_id'], input=dict(
                    planning_request=dict(schema_version='1.0.0', request_id=trend['request_id']),
                    trend_result=trend_out, inventory_product_result=inventory_out))
                contract.schema(request, 'planning-draft-request.schema.json')


if __name__ == '__main__':
    unittest.main()
