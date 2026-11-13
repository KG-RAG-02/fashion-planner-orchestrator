"""기획서 PNG (planning_sheet.py): the evidence-linked planning board drawn from a stored integrated envelope.

Presentation only: these tests pin that the sheet keeps the board's section order, shows producer
statuses and values unchanged, never invents Evidence IDs or zeros, and that the HTTP route reads the
store without changing it. PNG tests need a Korean font (fonts-nanum in the image, Apple SD Gothic Neo
on macOS); without one they are skipped with that reason.
"""
import copy
import io
import json
import sqlite3
import unittest
from pathlib import Path

from _http_support import LiveServerMixin, environment
from vogue_orchestrator.planning_sheet import (Fonts, PlanningSheetError, build_sheet, planning_sheet_png)
from vogue_orchestrator.workflow_store import WorkflowStore, digest

FIXTURES = Path(__file__).resolve().parents[1] / 'tools' / 'workflow_contract' / 'fixtures'
INTEGRATED = ('integrated-ok', 'integrated-image-failed', 'integrated-review-no-results', 'integrated-review-error',
              'integrated-stock-partial', 'integrated-trend-no-results', 'integrated-draft-no-results', 'integrated-auth-error',
              'integrated-visuals-ok')
SECTION_ORDER = ('concept', 'trend', 'inventory', 'sales_market', 'insight', 'specifications', 'references', 'finance')
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
    def test_every_fixture_keeps_the_board_sections_in_order(self):
        for name in INTEGRATED:
            with self.subTest(fixture=name):
                sheet = build_sheet(fixture(name))
                self.assertEqual(tuple(s.section_id for s in sheet.board.sections), SECTION_ORDER)
                self.assertEqual(sheet.board.process, ('ISSUE', 'INSIGHT', 'PROPOSAL'))
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
                self.assertLessEqual(set(sheet.board.evidence_ids), known)

    def test_producer_statuses_are_shown_unchanged(self):
        for name, section_id in (('integrated-stock-partial', 'inventory'), ('integrated-review-no-results', 'sales_market'),
                                 ('integrated-trend-no-results', 'trend'), ('integrated-draft-no-results', 'insight')):
            with self.subTest(fixture=name):
                envelope = fixture(name)
                stage_key = {'inventory': 'stock', 'sales_market': 'sales_market_review', 'trend': 'trend',
                             'insight': 'planning_draft'}[section_id]
                stage = next((s for s in envelope['result']['stages'] if s['stage'] == stage_key), None)
                section = {s.section_id: s for s in build_sheet(envelope).board.sections}[section_id]
                expected = stage['status'] if stage else 'not_provided'
                self.assertEqual(section.input_status, expected)
                if expected == 'no_results':
                    self.assertEqual(section.state, 'no_results')

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
        self.assertEqual((section.state, section.claims), ('failed', ()))
        self.assertIn('UPSTREAM_TIMEOUT: timed out', section.notes)

    def test_p06_concept_and_visuals_are_mapped_from_the_draft(self):
        # workflow-contract 0.2.0: draft.concept fills the spec section; draft.visuals are the panel; 06 is the sheet.
        sheet = build_sheet(fixture('integrated-visuals-ok'))
        spec = next(section for section in sheet.board.sections if section.section_id == 'specifications')
        texts = [claim.text for claim in spec.claims]
        self.assertEqual(texts[0], '상품 정의: 합성 셔츠 콘셉트 (fixture)')
        self.assertIn('color=화이트', texts)
        self.assertEqual([(v.key, v.status, v.model) for v in sheet.visual.draft_visuals],
                         [('product', 'generated', 'fixture'), ('outfit', 'generated', 'fixture')])
        self.assertEqual(sheet.visual.image_generation_calls, 2)
        self.assertEqual(sheet.stages[-1][:3], ('final_image', '06', 'Sheet'))
        self.assertEqual(dict(sheet.brief)['시안 (P06)'], '신상품 생성됨 · 착장 생성됨')
        self.assertEqual(build_sheet(fixture('integrated-ok')).visual.draft_visuals, ())  # earlier runs: final image panel

    def test_brief_rows_report_missing_fields_as_missing(self):
        brief = dict(build_sheet(fixture('integrated-ok')).brief)
        self.assertTrue(brief['점수'].startswith('미제공'))
        self.assertTrue(brief['후보 유형'].startswith('미제공'))
        self.assertEqual(set(brief), {'후보 ID', '후보 유형', '대상', '점수', '가정', '미확정', '리스크·한계', '검증 질문', '시안'})

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
            self.assertGreater(image.height, 2000)
            self.assertEqual(image.info['vogue:request_id'], envelope['result']['request_id'])
            self.assertEqual(image.info['vogue:format'], 'evidence-linked-planning-board/png-1')

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
            red, green, blue = image.convert('RGB').getpixel((2624, 960))  # centre of the visual box
        self.assertGreater(red, 180)
        self.assertLess(green, 80)

    def test_p06_visuals_are_embedded_and_the_final_image_never_is(self):
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
            left, right = rgb.getpixel((2378, 982)), rgb.getpixel((2870, 982))  # centres of the two panels
        self.assertTrue(left[0] > 180 and left[2] < 80, left)
        self.assertTrue(right[2] > 180 and right[0] < 80, right)

    def test_landscape_p06_visuals_are_stacked_full_width(self):
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
            top, bottom = rgb.getpixel((2624, 796)), rgb.getpixel((2624, 1488))  # centres of the two stacked panels
        self.assertTrue(top[0] > 180 and top[2] < 80, top)
        self.assertTrue(bottom[2] > 180 and bottom[0] < 80, bottom)

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
        with environment(VOGUE_WORKFLOW_STORE_DIR=str(self.store_dir)):
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
        self.assertEqual(response.getheader('Content-Disposition'), 'attachment; filename="vogue-planning-sheet-run-1.png"')
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
