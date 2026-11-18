"""SWOT draft for the planning sheet (swot.py): what is sent to the model, how the answer is checked, and that
stage 06 keeps one validated draft per run so the planning-sheet route renders it without calling the model."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from vogue_orchestrator.swot import (SwotRejected, SwotWriter, checkpoint_key, enabled, load_swot, parse_swot, prompt_sha256,
                                     swot_input, system_prompt)
from vogue_orchestrator.workflow_store import WorkflowStore

FIXTURES = Path(__file__).resolve().parents[1] / 'tools' / 'workflow_contract' / 'fixtures'
VALID = {'strengths': [{'text': '재고를 착장에 바로 쓸 수 있다 (가상 재고 기준).', 'basis': ['stock']}],
         'weaknesses': [{'text': '현재 자사 판매 데이터가 없다.', 'basis': ['market', 'market']}],
         'opportunities': [{'text': '트렌드가 컬렉션 전반에 보인다.', 'basis': ['trend']}],
         'threats': [],
         'promotions': [{'title': '착장 묶음 제안', 'text': '매칭 재고 팬츠와 신상품을 한 착장으로 보여준다.', 'basis': ['stock', 'product']}],
         'differentiation': [{'text': '재고 팬츠와 달리 자수 디테일을 더한다.', 'basis': ['product', 'stock']}]}


def envelope():
    value = copy.deepcopy(json.loads((FIXTURES / 'integrated-visuals-ok.json').read_text(encoding='utf-8')))
    stock = value['result']['sources']['stock']['raw_response']['result']
    stock['trend_results'][0]['reason'] = {'sell_through': 0.341, 'stock_value_krw': 20204400, 'score': 0.815,
                                           'recent_trend': {'weeks': 4, 'recent_sold': 0, 'prev_sold': 0, 'basis': 'too_few_sales'},
                                           'data_nature': {'sell_through': 'sample'}}
    value['result']['sources']['sales_market_review']['raw_response'] = json.loads(
        (FIXTURES / 'research-handoff.response.json').read_text(encoding='utf-8'))
    value['result']['sources']['sales_market_review']['raw_response']['result']['target_audience'] = {
        'status': 'ok', 'proposal': {'proposal': '합성 타깃', 'channel_content_hypotheses': ['합성 채널 가설'],
                                     'merchandising_actions': ['합성 운영 제안']}}
    value['result']['user_input']['text'] = 'RAW USER REQUEST'
    value['result']['planning_request']['query'] = 'RAW USER REQUEST'
    return value


class InputTests(unittest.TestCase):
    def test_the_summary_carries_trend_stock_indicators_product_and_limits_but_not_the_raw_request(self):
        summary = swot_input(envelope())
        self.assertEqual(summary['trend']['name'], '합성 셔츠')
        self.assertEqual(summary['stock']['quantity'], 7)
        self.assertEqual(summary['stock']['indicators']['sell_through'], 0.341)
        self.assertEqual(summary['stock']['indicators']['recent_trend']['basis'], 'too_few_sales')
        self.assertEqual(summary['stock']['indicators']['data_nature'], {'sell_through': 'sample'})
        self.assertEqual(summary['product']['definition'], '합성 셔츠 콘셉트 (fixture)')
        self.assertIn('limitations', summary)
        self.assertEqual(summary['market']['channel_content_hypotheses'], ['합성 채널 가설'])
        self.assertEqual(summary['market']['merchandising_actions'], ['합성 운영 제안'])
        self.assertNotIn('RAW USER REQUEST', json.dumps(summary, ensure_ascii=False))

    def test_the_p07_consumer_projection_reaches_the_market_summary(self):
        # P07 main c662d4d sends no fabric unit cost and no price_positioning; nothing stands in for them.
        value = envelope()
        value['result']['sources']['sales_market_review']['raw_response'] = json.loads(
            (FIXTURES / 'research-handoff.consumer-projection.response.json').read_text(encoding='utf-8'))
        market = swot_input(value)['market']
        self.assertEqual((market['proposed_price'], market['price_currency'], market['margin_rate_percent']), (66000, 'KRW', 56.88))
        self.assertEqual((market['target_age_cohort'], market['target_age_share']), ('20–39 (20s and 30s)', 0.5973))
        self.assertEqual(market['primary_segment'], '합성 예시: 출퇴근과 주말을 함께 입는 20–30대 여성')
        self.assertEqual(market['channel_content_hypotheses'], ['합성 예시: 출근룩 코디 콘텐츠', '합성 예시: 소재 클로즈업 이미지'])
        self.assertEqual(market['merchandising_actions'], ['합성 예시: 기본 컬러 2종 우선 검토', '합성 예시: 사이즈 구간 검증'])
        self.assertNotIn('fabric_unit_cost', market)
        self.assertNotIn('price_positioning', market)

    def test_a_draft_claim_repeating_the_request_is_not_sent(self):
        value = envelope()  # runs recorded before planning_analysis carry the request among the Draft claims
        body = value['result']['sources']['planning_draft']['raw_response']['result']
        body.pop('planning_analysis', None)
        body['draft']['claims'] = [{'kind': 'proposal', 'text': 'RAW USER REQUEST'}, {'kind': 'proposal', 'text': '합성 기획 제안'}]
        summary = swot_input(value)
        self.assertEqual(summary['product']['planning_points'], ['합성 기획 제안'])
        self.assertNotIn('RAW USER REQUEST', json.dumps(summary, ensure_ascii=False))

    def test_the_prompt_file_exists_and_names_the_output_keys(self):
        prompt = system_prompt()
        for key in ('strengths', 'weaknesses', 'opportunities', 'threats', 'promotions', 'title', 'differentiation', 'basis'):
            self.assertIn(key, prompt)

    def test_enabled_unless_set_to_zero(self):
        self.assertTrue(enabled({}))
        self.assertFalse(enabled({'VOGUE_SWOT_ENABLED': '0'}))


class ParseTests(unittest.TestCase):
    def test_a_valid_answer_is_kept_with_duplicate_bases_folded(self):
        swot = parse_swot('```json\n' + json.dumps(VALID, ensure_ascii=False) + '\n```')
        self.assertEqual(swot['weaknesses'][0]['basis'], ['market'])
        self.assertEqual(swot['threats'], [])
        self.assertEqual(swot['promotions'][0]['title'], '착장 묶음 제안')
        self.assertEqual(swot['differentiation'][0]['basis'], ['product', 'stock'])
        legacy = {key: value for key, value in VALID.items() if key not in ('promotions', 'differentiation')}  # prompt v1
        self.assertEqual((parse_swot(json.dumps(legacy, ensure_ascii=False))['promotions'],
                          parse_swot(json.dumps(legacy, ensure_ascii=False))['differentiation']), ([], []))

    def test_invalid_answers_are_rejected_with_a_code(self):
        too_many = dict(VALID, strengths=[VALID['strengths'][0]] * 5)
        bad_basis = dict(VALID, threats=[{'text': '경쟁사 A가 있다.', 'basis': ['competitor']}])
        no_basis = dict(VALID, threats=[{'text': '근거 없는 문장', 'basis': []}])
        long_text = dict(VALID, threats=[{'text': '가' * 200, 'basis': ['market']}])
        empty = {key: [] for key in VALID}
        promo = VALID['promotions'][0]
        too_many_promotions = dict(VALID, promotions=[promo] * 4)
        promo_no_title = dict(VALID, promotions=[dict(promo, title=' ')])
        promo_bad_basis = dict(VALID, promotions=[dict(promo, basis=['budget'])])
        point = VALID['differentiation'][0]
        too_many_points = dict(VALID, differentiation=[point] * 4)
        point_bad_basis = dict(VALID, differentiation=[dict(point, basis=['competitor'])])
        cases = {'not json': 'SWOT_NOT_JSON', json.dumps({'strengths': []}): 'SWOT_SHAPE_INVALID',
                 json.dumps(too_many): 'SWOT_SHAPE_INVALID', json.dumps(bad_basis): 'SWOT_BASIS_INVALID',
                 json.dumps(no_basis): 'SWOT_BASIS_INVALID', json.dumps(long_text): 'SWOT_ITEM_INVALID',
                 json.dumps(empty): 'SWOT_EMPTY', json.dumps(too_many_promotions): 'PROMOTION_SHAPE_INVALID',
                 json.dumps(promo_no_title): 'PROMOTION_ITEM_INVALID', json.dumps(promo_bad_basis): 'PROMOTION_BASIS_INVALID',
                 json.dumps(too_many_points): 'DIFFERENTIATION_SHAPE_INVALID',
                 json.dumps(point_bad_basis): 'DIFFERENTIATION_BASIS_INVALID'}
        for text, code in cases.items():
            with self.subTest(code=code), self.assertRaises(SwotRejected) as caught:
                parse_swot(text)
            self.assertEqual(caught.exception.code, code)


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name).resolve())

    def test_the_model_is_asked_once_and_the_draft_is_reloaded_without_it(self):
        calls = []

        def call(system, user):
            calls.append((system, json.loads(user)))
            return json.dumps(VALID, ensure_ascii=False)

        writer = SwotWriter(self.store, call, model='m-test')
        first = writer('run-1', envelope())
        self.assertEqual(first['swot']['strengths'], VALID['strengths'])
        self.assertEqual((first['model'], first['prompt_sha256']), ('m-test', prompt_sha256()))
        self.assertEqual(writer('run-1', envelope()), first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], system_prompt())
        self.assertEqual(load_swot(self.store, 'run-1'), first)
        self.assertIsNone(load_swot(self.store, 'run-2'))

    def test_a_failed_answer_leaves_no_draft_and_a_retry_asks_again(self):
        answers = iter(['not json', json.dumps(VALID, ensure_ascii=False)])
        writer = SwotWriter(self.store, lambda system, user: next(answers), model='m')
        self.assertIsNone(writer('run-3', envelope()))
        self.assertEqual(self.store.checkpoint(checkpoint_key('run-3')), ('failed', {'reason': 'SWOT_NOT_JSON'}))
        self.assertIsNone(load_swot(self.store, 'run-3'))
        self.assertIsNotNone(writer('run-3', envelope()))

    def test_a_provider_error_is_recorded_by_class_name_only(self):
        def broken(system, user):
            raise TimeoutError('secret detail')
        self.assertIsNone(SwotWriter(self.store, broken)('run-4', envelope()))
        self.assertEqual(self.store.checkpoint(checkpoint_key('run-4')), ('failed', {'reason': 'TimeoutError'}))


class DifferentiationTests(unittest.TestCase):
    def test_the_stand_in_is_drawn_only_while_new_product_agent_has_none(self):
        from vogue_orchestrator import planning_sheet as ps
        drawn = []
        original = ps._bullets

        def spy(c, x, y, w, items, **kwargs):
            drawn.append(tuple(items))
            return original(c, x, y, w, items, **kwargs)

        try:
            fonts = ps.Fonts()
        except (ps.PlanningSheetError, ImportError):
            self.skipTest('no Korean font or Pillow')
        record = {'swot': parse_swot(json.dumps(VALID, ensure_ascii=False))}
        value = envelope()
        draft = value['result']['sources']['planning_draft']['raw_response']['result']['draft']
        draft['relation_to_own_products'] = [{'value': '합성 재고 관계', 'kind': 'evidence_observation', 'evidence_ids': []}]
        ps._bullets = spy
        try:
            ps.planning_sheet_png(value, fonts=fonts, swot=record)
            self.assertIn(('재고 팬츠와 달리 자수 디테일을 더한다.',), drawn)
            drawn.clear()
            draft['differentiation'] = [{'value': 'new-product-agent 차별점', 'kind': 'proposal', 'evidence_ids': []}]
            ps.planning_sheet_png(value, fonts=fonts, swot=record)
            self.assertIn(('new-product-agent 차별점',), drawn)
            self.assertNotIn(('재고 팬츠와 달리 자수 디테일을 더한다.',), drawn)
        finally:
            ps._bullets = original


class PosterTests(unittest.TestCase):
    def test_the_swot_grid_is_drawn_under_the_stock_product(self):
        from vogue_orchestrator.planning_sheet import Fonts, PlanningSheetError, planning_sheet_png
        try:
            fonts = Fonts()
        except (PlanningSheetError, ImportError):
            self.skipTest('no Korean font or Pillow')
        from PIL import Image

        def grey(artifact):
            out = io.BytesIO()
            Image.new('RGB', (30, 40), (200, 200, 200)).save(out, format='PNG')
            return out.getvalue()

        value = envelope()
        record = {'swot': parse_swot(json.dumps(VALID, ensure_ascii=False)), 'model': 'm', 'prompt_sha256': prompt_sha256()}
        with_swot = planning_sheet_png(value, load_artifact=grey, fonts=fonts, swot=record)
        without = planning_sheet_png(value, load_artifact=grey, fonts=fonts)
        self.assertNotEqual(with_swot, without)
        with Image.open(io.BytesIO(with_swot)) as image, Image.open(io.BytesIO(without)) as plain:
            self.assertGreater(image.height, plain.height)
            colours = {image.convert('RGB').getpixel((80, y)) for y in range(plain.height - 400, image.height - 100, 4)}
        self.assertIn((0xE6, 0xF2, 0xEA), colours)  # the strengths cell tint


if __name__ == '__main__':
    unittest.main()
