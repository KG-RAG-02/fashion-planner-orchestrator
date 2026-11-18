"""기획서 PNG (planning_sheet.py): the one-product proposal poster drawn from a stored integrated envelope.

Presentation only: these tests pin that the poster shows producer values unchanged, never invents Evidence
IDs or zeros, says why a section is empty, and that the HTTP route reads the store without changing it. PNG tests need a Korean font (fonts-nanum in the image, Apple SD Gothic Neo
on macOS); without one they are skipped with that reason.
"""
import copy
import importlib.util
import io
import json
import sqlite3
import unittest
from pathlib import Path

from _http_support import LiveServerMixin, environment
from fashion_planner_orchestrator.planning_sheet import (Fonts, PlanningSheetError, build_sheet, planning_sheet_png)
from fashion_planner_orchestrator.workflow_store import WorkflowStore, digest

FIXTURES = Path(__file__).resolve().parents[1] / 'tools' / 'workflow_contract' / 'fixtures'
INTEGRATED = ('integrated-ok', 'integrated-image-failed', 'integrated-review-no-results', 'integrated-review-error',
              'integrated-stock-partial', 'integrated-trend-no-results', 'integrated-draft-no-results', 'integrated-auth-error',
              'integrated-visuals-ok')
SECTION_ORDER = ('trend', 'inventory', 'sales_market', 'insight', 'finance')
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def fixture(name):
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def red_png(size=12, colour=(220, 20, 20)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (size, size), colour).save(out, format='PNG')
    return out.getvalue()


class FontMixin:
    def fonts(self):
        try:
            return Fonts()
        except PlanningSheetError as exc:
            self.skipTest(f'no Korean font for the planning sheet ({exc.code}); install fonts-nanum')
        except ImportError:
            self.skipTest('Pillow (workflow extra) is not installed')


class BoardMappingTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'jsonschema not installed (install the `workflow` extra)')
    def test_every_fixture_is_a_current_integrated_result(self):
        # The sheet fixtures are stored envelopes; they follow the current schema, not an older record format.
        from fashion_planner_orchestrator.workflow_contract import WorkflowContract
        contract = WorkflowContract()
        for name in INTEGRATED:
            with self.subTest(fixture=name):
                contract.schema(fixture(name), 'integrated-result.schema.json')

    def test_every_fixture_keeps_the_board_sections_in_order(self):
        for name in INTEGRATED:
            with self.subTest(fixture=name):
                sheet = build_sheet(fixture(name))
                self.assertEqual(tuple(s.section_id for s in sheet.board.sections), SECTION_ORDER)
                self.assertEqual(sheet.board.review_label, 'MD 검토용 초안 · 자동 승인 아님')

    def test_evidence_ids_are_never_invented(self):
        for name in INTEGRATED:
            with self.subTest(fixture=name):
                envelope = fixture(name)
                known = set(strings(envelope))
                sheet = build_sheet(envelope)
                for section in sheet.board.sections:
                    for claim in section.claims:
                        self.assertLessEqual(set(claim.evidence_ids), known, (section.section_id, claim.text))
                if sheet.concept_line:
                    self.assertLessEqual(set(sheet.concept_line.evidence_ids), known)

    def test_an_empty_section_says_why(self):
        for name, section_id in (('integrated-review-no-results', 'sales_market'), ('integrated-trend-no-results', 'trend'),
                                 ('integrated-draft-no-results', 'insight')):
            with self.subTest(fixture=name):
                section = {s.section_id: s for s in build_sheet(fixture(name)).board.sections}[section_id]
                self.assertTrue(section.notes, 'a no_results section carries the producer note or the reason it is empty')

    def test_a_missing_metric_stays_missing_and_is_never_zero(self):
        envelope = copy.deepcopy(fixture('integrated-ok'))
        review = envelope['result']['sources']['sales_market_review']['raw_response']['result']['review']
        review['metrics'] = [{'name': 'current_company_sales', 'value': None, 'missing_reason': 'NO_SOURCE', 'unit': None,
                              'currency': None, 'period': None, 'evidence_refs': [], 'observation': None}]
        section = {s.section_id: s for s in build_sheet(envelope).board.sections}['sales_market']
        texts = [claim.text for claim in section.claims]
        self.assertIn('현재 자사 판매: 미확인 — NO_SOURCE', texts)
        self.assertFalse(any(' 0' in text or text.endswith(': 0') for text in texts))

    def test_a_failed_stage_is_reported_without_content(self):
        envelope = copy.deepcopy(fixture('integrated-ok'))
        result = envelope['result']
        del result['sources']['sales_market_review']
        for stage in result['stages']:
            if stage['stage'] == 'sales_market_review':
                stage['status'] = 'error'
        result['errors'] = [{'stage': 'sales_market_review', 'code': 'UPSTREAM_TIMEOUT', 'message': 'timed out',
                             'fatal': True, 'retryable': True}]
        section = {s.section_id: s for s in build_sheet(envelope).board.sections}['sales_market']
        self.assertEqual(section.claims, ())
        self.assertIn('UPSTREAM_TIMEOUT: timed out', section.notes)

    def test_novelty_concept_and_visuals_are_mapped_from_the_draft(self):
        # draft.concept gives the product definition; draft.visuals are the LOOK images.
        sheet = build_sheet(fixture('integrated-visuals-ok'))
        self.assertEqual(sheet.concept_line.text, '합성 셔츠 콘셉트 (fixture)')
        self.assertEqual([(v.key, v.status, v.model) for v in sheet.visual.draft_visuals],
                         [('product', 'generated', 'fixture'), ('outfit', 'generated', 'fixture')])
        self.assertEqual(build_sheet(fixture('integrated-ok')).visual.draft_visuals, ())  # no visuals: one placeholder panel

    def test_research_price_cost_margin_and_target_are_read_as_reported(self):
        envelope = copy.deepcopy(fixture('integrated-visuals-ok'))
        research = json.loads((FIXTURES / 'research-handoff.response.json').read_text(encoding='utf-8'))
        body = research['result']
        body['price_analysis'].update(proposed_price=59000, currency='KRW', unit='KRW')
        # research sets fabric_cost_analysis.unit_cost when a fabric listing gives a per-metre reference.
        body['fabric_cost_analysis'].update(unit_cost=12450.0, unit='KRW/m', currency='KRW')
        body['margin_analysis'].update(margin_rate_percent=56.88)
        body['target_audience'] = {
            'status': 'ok', 'limitations': [], 'evidence_ids': [],
            'age_cohort': {'age_band': '20_39', 'definition': '20–39 (20s and 30s)',
                           'share_of_customers_with_known_age': 0.597291, 'suppressed': False},
            'proposal': {'proposal': '합성 타깃 가설 문장', 'primary_segment': '합성 주 타깃',
                         'secondary_segments': ['보조 타깃'], 'product_fit': {'wearing_occasions': ['출퇴근', '주말']}}}
        envelope['result']['sources']['sales_market_review']['raw_response'] = research
        sheet = build_sheet(envelope)
        self.assertEqual(sheet.price_rows, (('제안 판매가', '59,000원'), ('소재 원가', '12,450원/m'), ('마진율', '56.88%')))
        target = sheet.target
        self.assertEqual((target.age, target.age_note), ('20–39 (20s and 30s)', '연령 확인 고객의 59.7%'))
        self.assertEqual((target.primary, target.summary, target.secondary, target.occasions),
                         ('합성 주 타깃', '합성 타깃 가설 문장', ('보조 타깃',), ('출퇴근', '주말')))

    def test_research_consumer_projection_fills_the_market_column(self):
        # research's consumer projection: Korean limitations, no fabric unit_cost (listing references are not verified costs, so
        # the sheet shows no 소재 원가 row), compact evidence, target hypothesis without price_positioning.
        envelope = copy.deepcopy(fixture('integrated-visuals-ok'))
        research = fixture('research-handoff.consumer-projection.response')
        envelope['result']['sources']['sales_market_review']['raw_response'] = research
        sheet = build_sheet(envelope)
        self.assertEqual(sheet.price_rows, (('제안 판매가', '66,000원'), ('업계 평균 마진 참고치', '56.88%')))
        target = sheet.target
        self.assertEqual((target.age, target.age_note), ('20–39 (20s and 30s)', '연령 확인 고객의 59.7%'))
        self.assertEqual((target.primary, target.summary), ('합성 예시: 출퇴근과 주말을 함께 입는 20–30대 여성',
                                                           '합성 예시: 20–30대 여성 직장인을 위한 울 혼방 테일러드 재킷 가설입니다.'))
        self.assertEqual((target.secondary, target.occasions),
                         (('합성 예시: 40대 초반 미니멀 스타일 선호 고객',), ('출퇴근', '주말 외출', '격식 있는 모임')))
        sales = {section.section_id: section for section in sheet.board.sections}['sales_market']
        # The market column reads the consumer projection's comparison summaries as producer findings.
        self.assertEqual([(claim.text, claim.kind) for claim in sales.claims], [
            ('H&M 과거 거래가 참고: 거래 표본 1,200건 · 중앙값 44.25 USD · 기간 2018-09-20–2020-09-22 · ×590 연구 환산',
             'producer_finding'),
            ('원단 공개 목록: wool 참고 · 공개 원단 목록 1건 · 중앙값 20,000 KRW/m', 'producer_finding'),
            ('원단 공개 목록: wool 참고 · 공개 원단 목록 1건 · 중앙값 26,000 KRW/m', 'producer_finding'),
            ('H&M 과거 고객 연령 집계: 최대 연령 코호트 20–39 (20s and 30s) · 연령 확인 고객 4,000명 기준 · '
             '기간 2018-09-20–2020-09-22', 'producer_finding')])
        self.assertEqual(sales.notes, tuple(research['result']['limitations']))  # Korean, in research's order
        self.assertEqual(sales.notes[0], '[마진 참고] 미국 의류 업계 평균 매출총이익률 벤치마크이며 이 상품의 실제 마진이 아닙니다.')

    def test_a_missing_target_stays_missing(self):
        envelope = copy.deepcopy(fixture('integrated-visuals-ok'))
        envelope['result']['sources']['sales_market_review']['raw_response'] = json.loads(
            (FIXTURES / 'research-handoff.response.json').read_text(encoding='utf-8'))
        sheet = build_sheet(envelope)
        self.assertIsNone(sheet.target)  # the synthetic research response carries no target analysis
        self.assertEqual(sheet.price_rows, ())

    def test_the_current_new_product_draft_fills_the_design_column(self):
        envelope = copy.deepcopy(fixture('integrated-visuals-ok'))
        body = envelope['result']['sources']['planning_draft']['raw_response']['result']
        draft = body['draft']
        draft.pop('concept', None)
        draft.pop('claims', None)
        claim = lambda value, kind='proposal', ids=(): {'value': value, 'kind': kind, 'evidence_ids': list(ids)}
        draft.update(
            product_definition=claim('합성 가디건 콘셉트'), opportunity=claim('합성 기획 기회'),
            opportunity_findings=[{'statement': claim('합성 근거 문장', 'evidence_observation', ['trend:fixture:article:0:bd1ee873'])}],
            product_profile={'category': claim('가디건', 'user_requested'), 'season': claim('2026FW', 'user_requested'),
                             'product_type': claim(None, 'unknown'), 'gender': claim('women', 'user_requested'),
                             'silhouette': claim('오버사이즈', 'evidence_observation'), 'color': claim('브라운', 'to_validate'),
                             'material': claim(None, 'unknown'),
                             'details': [{'name': 'detail', 'value': '자수', 'kind': 'evidence_observation', 'evidence_ids': []}]},
            attributes=[{'name': 'material', 'value': '재고 소재', 'kind': 'to_validate', 'evidence_ids': []}],
            differentiation=[claim('합성 차별점')], relation_to_own_products=[claim('합성 재고 관계', 'evidence_observation')],
            questions_for_validation=['합성 확인 질문'])
        body['planning_analysis'] = {'proposals': [claim('합성 가디건 콘셉트'), claim('합성 기획 제안')],
                                     'interpretations': [claim('합성 기획 기회'), claim(None, 'unknown')]}
        sheet = build_sheet(envelope)
        self.assertEqual(sheet.concept_line.text, '합성 가디건 콘셉트')
        self.assertEqual([(row.label, row.value, row.kind) for row in sheet.spec_rows],
                         [('카테고리', '가디건', 'user_requested'), ('시즌', '2026FW', 'user_requested'), ('성별', '여성', 'user_requested'),
                          ('실루엣', '오버사이즈', 'evidence_observation'), ('컬러', '브라운', 'to_validate'),
                          ('디테일', '자수', 'evidence_observation')])
        self.assertNotIn('재고 소재', [row.value for row in sheet.spec_rows])  # draft.attributes is not the product spec
        concept = sheet.concept
        self.assertEqual((concept.opportunity, concept.findings), ('합성 기획 기회', ('합성 근거 문장',)))
        self.assertEqual((concept.differentiation, concept.own_products), (('합성 차별점',), ('합성 재고 관계',)))
        self.assertEqual(sheet.insight_points, ('합성 기획 제안',))  # definition and opportunity are not repeated
        self.assertIn('합성 확인 질문', sheet.review_items)

    def test_source_ids_in_parentheses_are_dropped_from_concept_sentences(self):
        envelope = copy.deepcopy(fixture('integrated-visuals-ok'))
        draft = envelope['result']['sources']['planning_draft']['raw_response']['result']['draft']
        draft.pop('concept', None)
        claim = lambda value: {'value': value, 'kind': 'proposal', 'evidence_ids': []}  # noqa: E731
        draft.update(product_definition=claim('트렌드 방향: 화이트 (trend_analysis_result), 레드 (trend_analysis_result).'),
                     opportunity=claim('합성 기회 (sample) 문장 (stock_analysis_result)'))
        sheet = build_sheet(envelope)
        self.assertEqual(sheet.concept_line.text, '트렌드 방향: 화이트, 레드.')
        self.assertEqual(sheet.concept.opportunity, '합성 기회 (sample) 문장')  # a plain word in parentheses stays

    def test_invalid_input_is_refused(self):
        with self.assertRaises(PlanningSheetError) as caught:
            build_sheet({'result': None})
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('PLANNING_SHEET_INPUT_INVALID', 422))


class PngRenderingTests(FontMixin, unittest.TestCase):
    def test_png_is_deterministic_and_tagged(self):
        fonts = self.fonts()
        envelope = fixture('integrated-ok')
        first = planning_sheet_png(envelope, fonts=fonts)
        self.assertEqual(first, planning_sheet_png(envelope, fonts=fonts))
        self.assertTrue(first.startswith(PNG_SIGNATURE))
        from PIL import Image
        with Image.open(io.BytesIO(first)) as image:
            self.assertEqual(image.width, 3200)
            self.assertGreater(image.height, 1500)
            self.assertEqual(image.info['fashion-planner:request_id'], envelope['result']['request_id'])
            self.assertEqual(image.info['fashion-planner:format'], 'planning-sheet')

    def test_the_verified_final_image_is_embedded(self):
        fonts = self.fonts()
        envelope = fixture('integrated-ok')
        calls = []

        def load(artifact):
            calls.append(artifact)
            return red_png()

        png = planning_sheet_png(envelope, load_artifact=load, fonts=fonts)
        self.assertEqual(calls, [envelope['result']['final_image']['artifact']])
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            red, green, blue = image.convert('RGB').getpixel((1600, 1002))  # centre of the image in the Design column
        self.assertGreater(red, 180)
        self.assertLess(green, 80)

    def test_novelty_visuals_are_embedded_and_the_final_image_never_is(self):
        fonts = self.fonts()
        envelope = fixture('integrated-visuals-ok')
        visuals = envelope['result']['sources']['planning_draft']['raw_response']['result']['draft']['visuals']
        colours = {visuals['product']['artifact']['sha256']: (220, 20, 20), visuals['outfit']['artifact']['sha256']: (20, 40, 220)}
        calls = []

        def load(artifact):
            calls.append(artifact)
            return red_png(colour=colours[artifact['sha256']])

        png = planning_sheet_png(envelope, load_artifact=load, fonts=fonts)
        self.assertEqual(calls, [visuals['product']['artifact'], visuals['outfit']['artifact']])  # never the final image
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            rgb = image.convert('RGB')
            left, right = rgb.getpixel((1346, 978)), rgb.getpixel((1852, 978))  # centres of LOOK 01 and LOOK 02
        self.assertTrue(left[0] > 180 and left[2] < 80, left)
        self.assertTrue(right[2] > 180 and right[0] < 80, right)

    def test_landscape_novelty_visuals_are_stacked_full_width(self):
        # OpenAI returns landscape visuals (e.g. 1536x1024); side by side they would be shrunk to a quarter.
        fonts = self.fonts()
        envelope = fixture('integrated-visuals-ok')
        visuals = envelope['result']['sources']['planning_draft']['raw_response']['result']['draft']['visuals']
        colours = {visuals['product']['artifact']['sha256']: (220, 20, 20), visuals['outfit']['artifact']['sha256']: (20, 40, 220)}

        def load(artifact):
            from PIL import Image
            out = io.BytesIO()
            Image.new('RGB', (30, 12), colours[artifact['sha256']]).save(out, format='PNG')
            return out.getvalue()

        from PIL import Image
        with Image.open(io.BytesIO(planning_sheet_png(envelope, load_artifact=load, fonts=fonts))) as image:
            rgb = image.convert('RGB')
            top, bottom = rgb.getpixel((1600, 944)), rgb.getpixel((1600, 1684))  # centres of the two stacked LOOK panels
        self.assertTrue(top[0] > 180 and top[2] < 80, top)
        self.assertTrue(bottom[2] > 180 and bottom[0] < 80, bottom)

    def test_trend_photos_are_drawn_in_the_analysis_column(self):
        fonts = self.fonts()
        envelope = fixture('integrated-visuals-ok')
        green = (20, 170, 40)

        def photo(colour):
            from PIL import Image
            out = io.BytesIO()
            Image.new('RGB', (30, 40), colour).save(out, format='PNG')
            return out.getvalue()

        photos = [(photo(green), 'https://img.vogue.co.kr/a.jpg'), (photo(green), 'https://img.vogue.co.kr/b.jpg')]
        png = planning_sheet_png(envelope, load_artifact=lambda artifact: photo((200, 200, 200)), fonts=fonts,
                                 trend_photos=photos)
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            rgb = image.convert('RGB')
            left, right = rgb.getpixel((300, 1070)), rgb.getpixel((800, 1070))  # the two photos under the trend
        self.assertEqual((left, right), (green, green))
        self.assertNotEqual(png, planning_sheet_png(envelope, load_artifact=lambda artifact: photo((200, 200, 200)), fonts=fonts))

    def test_the_research_consumer_projection_renders_deterministically(self):
        fonts = self.fonts()
        envelope = copy.deepcopy(fixture('integrated-visuals-ok'))
        envelope['result']['sources']['sales_market_review']['raw_response'] = fixture('research-handoff.consumer-projection.response')
        png = planning_sheet_png(envelope, load_artifact=lambda artifact: red_png(), fonts=fonts)
        self.assertTrue(png.startswith(PNG_SIGNATURE))
        self.assertEqual(png, planning_sheet_png(envelope, load_artifact=lambda artifact: red_png(), fonts=fonts))

    def test_a_failed_image_is_never_loaded(self):
        fonts = self.fonts()

        def load(artifact):
            raise AssertionError('a failed image must not be read')

        png = planning_sheet_png(fixture('integrated-image-failed'), load_artifact=load, fonts=fonts)
        self.assertTrue(png.startswith(PNG_SIGNATURE))


class PlanningSheetRouteTests(FontMixin, LiveServerMixin, unittest.TestCase):
    def setUp(self):
        self.start_server()
        self.store_dir = self.state / 'workflow'
        self.store = WorkflowStore(self.store_dir)

    def call(self, method, path):
        with environment(ORCHESTRATOR_WORKFLOW_STORE_DIR=str(self.store_dir)):
            return self.request(method, path)

    def save(self, rid, envelope, *, final=True):
        envelope = copy.deepcopy(envelope)
        envelope['request_id'] = envelope['result']['request_id'] = rid
        self.store.bind(rid, digest({'rid': rid}))
        self.store.save_run(rid, 'progress', {'http_status': 200, 'envelope': envelope})
        if final:
            self.store.save_run(rid, 'current', {'http_status': 200, 'envelope': envelope})
        return envelope

    def rows(self):
        with sqlite3.connect(self.store_dir / 'checkpoints.sqlite3') as db:
            return (db.execute('SELECT rid, attempt, value FROM runs ORDER BY rid, attempt').fetchall(),
                    db.execute('SELECT * FROM md_decisions ORDER BY rid, sequence').fetchall())

    def test_serves_the_png_of_a_finished_run_without_touching_the_store(self):
        self.fonts()
        envelope = fixture('integrated-ok')
        artifact = self.store.put(red_png(), 'image/png', owner='run-1')
        envelope = copy.deepcopy(envelope)
        envelope['result']['final_image']['artifact'] = artifact
        self.save('run-1', envelope)
        before = self.rows()
        status, body, response = self.call('GET', '/api/workflow/runs/run-1/planning-sheet.png')
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'image/png')
        self.assertEqual(response.getheader('Content-Disposition'), 'attachment; filename="planning-sheet-run-1.png"')
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertTrue(body.startswith(PNG_SIGNATURE))
        self.assertEqual(self.rows(), before)

    def test_unknown_unfinished_and_wrong_method(self):
        self.save('run-2', fixture('integrated-ok'), final=False)
        status, problem, _ = self.call('GET', '/api/workflow/runs/run-2/planning-sheet.png')
        self.assertEqual((status, problem['error']['code']), (409, 'RUN_NOT_FINISHED'))
        status, problem, _ = self.call('GET', '/api/workflow/runs/absent/planning-sheet.png')
        self.assertEqual((status, problem['error']['code']), (404, 'RUN_NOT_FOUND'))
        status, problem, response = self.call('POST', '/api/workflow/runs/run-2/planning-sheet.png')
        self.assertEqual((status, problem['error']['code'], response.getheader('Allow')), (405, 'METHOD_NOT_ALLOWED', 'GET'))


if __name__ == '__main__':
    unittest.main()
