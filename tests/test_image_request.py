"""Final-image input package and Images API adapters (image_request.py, workflows/image_generation.py).

Pins the 2026-11-13 decisions: verbatim values (no summarising), P07 raw metrics/evidence excluded from
the image request only, evidence as ID codes, exact duplicates once, integrity metadata kept out of the
prompt, product photos as reference images, the first three trends only, and no HTTP call for a prompt
over the documented 32,000 characters. No network: urlopen is patched.
"""
import base64
import copy
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from _http_support import environment
from vogue_orchestrator import workflow_http
from vogue_orchestrator.image_request import MAX_TRENDS, META_KEYS, build_image_input
from vogue_orchestrator.workflows.image_generation import (IMAGE_PROMPT_MAX_CHARS, ImageProviderError,
                                                           OpenAIImageEditProvider, OpenAIImageProvider,
                                                           compose_full_prompt)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURES = ROOT / 'tools' / 'workflow_contract' / 'fixtures'
INTEGRATED = ('integrated-ok', 'integrated-image-failed', 'integrated-review-no-results', 'integrated-stock-partial')
SECTION_ORDER = ['1_request', '2_draft', '3_trend', '4_stock', '5_review', '6_limitations']
SHA256 = re.compile(r'[0-9a-f]{64}')


def image_request(envelope):
    result = envelope['result']
    sources = result['sources']
    return dict(request_id=result['request_id'], draft_response_sha256=sources['planning_draft']['content_sha256'],
                review_response_sha256=sources['sales_market_review']['content_sha256'], integration=result.get('integration'),
                lineage=(result.get('final_image') or {}).get('lineage'), planning_draft=sources['planning_draft'],
                sales_market_review=sources['sales_market_review'])


def fixture(name):
    return json.loads((CONTRACT_FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def jpeg_bytes(colour=(200, 30, 30)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (16, 24), colour).save(out, format='JPEG')
    return out.getvalue()


def inner(request):
    """(draft result, trend result, stock result) inside a reconstructed image request."""
    draft = request['planning_draft']['raw_response']['result']
    inputs = draft['source_request']['input']
    return draft, inputs['trend_response']['raw_response']['result'], inputs['stock_response']['raw_response']['result']


OWN_TEXT = {'P07 원본 지표(metrics)·근거(evidence)는 이미지 요청에서 제외했습니다. 원문은 실행 기록에 보존된 P07 응답에 있습니다.'}


class PackageTests(unittest.TestCase):
    def test_sections_are_ordered_core_first_and_carry_no_integrity_metadata(self):
        for name in INTEGRATED:
            with self.subTest(fixture=name):
                package = build_image_input(image_request(fixture(name)))
                self.assertEqual(list(package.sections), SECTION_ORDER)
                self.assertIsNone(SHA256.search(package.full_prompt))
                self.assertNotIn('artifact://', package.full_prompt)
                for key in ('content_sha256', 'raw_artifact', 'lineage', 'source_pointer'):
                    self.assertNotIn(f'"{key}"', package.planning_json)
                self.assertLess(len(package.full_prompt), IMAGE_PROMPT_MAX_CHARS)
                self.assertEqual(package.full_prompt, compose_full_prompt(package.system_prompt, package.user_prompt))
                self.assertEqual(package.prompt_version, 'p08-reviewed-image/2')

    def test_every_value_is_verbatim_and_every_code_resolves(self):
        for name in INTEGRATED:
            with self.subTest(fixture=name):
                request = image_request(fixture(name))
                package = build_image_input(request)
                originals = set(strings(request))
                for value in strings(package.sections):
                    if value not in OWN_TEXT and not value.startswith('Trend 응답의 트렌드'):
                        self.assertIn(value, originals)
                draft, trend, stock = inner(request)
                trend_ids = {item['evidence_id'] for item in trend.get('evidence', [])}
                stock_ids = {item['evidence_id'] for item in stock.get('evidence', [])}
                for claim in package.sections['2_draft']['claims']:
                    self.assertLessEqual(set(claim['evidence']), trend_ids | stock_ids)
                self.assertLessEqual(set(package.sections['3_trend']['evidence_codes']), trend_ids)
                self.assertLessEqual(set(package.sections['4_stock']['evidence_codes']), stock_ids)

    def test_the_build_is_deterministic(self):
        request = image_request(fixture('integrated-ok'))
        self.assertEqual(build_image_input(request).full_prompt, build_image_input(copy.deepcopy(request)).full_prompt)

    def test_p07_raw_metrics_and_evidence_never_reach_the_prompt(self):
        request = image_request(fixture('integrated-ok'))
        review = request['sales_market_review']['raw_response']['result']['review']
        review['metrics'] = [dict(name='sales_data.purchase_rows', value=1000 + i, unit='purchase_rows', period=f'2020-01-{i % 28 + 1:02d}',
                                  evidence_refs=[{'evidence_id': f'E-SALES-{i}'}]) for i in range(1028)]
        review['evidence'] = [{'evidence_id': f'E-SALES-{i}', 'metric_definition': 'synthetic row'} for i in range(1033)]
        package = build_image_input(request)
        self.assertNotIn('E-SALES-', package.full_prompt)
        self.assertNotIn('purchase_rows', package.planning_json)
        self.assertEqual(package.provenance['excluded']['sales_market_review.review.metrics'], 1028)
        self.assertEqual(package.provenance['excluded']['sales_market_review.review.evidence'], 1033)
        self.assertLess(len(package.full_prompt), IMAGE_PROMPT_MAX_CHARS)

    def test_limitations_appear_once_and_integration_claims_are_not_repeated(self):
        request = image_request(fixture('integrated-ok'))
        package = build_image_input(request)
        limitations = package.sections['6_limitations']
        self.assertEqual(len(limitations), len(set(limitations)))
        self.assertNotIn('integration', package.sections)
        draft, _, _ = inner(request)
        findings = request['sales_market_review']['raw_response']['result']['review']['findings']
        for claim in [*request['integration']['claims'], *draft['draft']['claims'], *findings]:
            self.assertEqual(package.planning_json.count(json.dumps(claim['text'], ensure_ascii=False)), 1, claim['text'])
        # In a real run integration.claims == draft.claims + review.findings: nothing is sent twice.
        request['integration']['claims'] = copy.deepcopy(draft['draft']['claims'] + findings)
        rebuilt = build_image_input(request)
        self.assertNotIn('integration_claims', rebuilt.sections['2_draft'])
        self.assertEqual(rebuilt.provenance['excluded']['integration.claims'], len(request['integration']['claims']))

    def test_product_photos_become_reference_images_once(self):
        request = image_request(fixture('integrated-ok'))
        _, _, stock = inner(request)
        photo = jpeg_bytes()
        product = stock['products'][0]
        product['image'] = dict(media_type='image/jpeg', encoding='base64', data=base64.b64encode(photo).decode(), source='H&M images/0001.jpg')
        stock['products'].append(copy.deepcopy(product))  # same photo used twice: sent once
        stock['trend_results'].append(dict(copy.deepcopy(stock['trend_results'][0]), product_index=1))
        package = build_image_input(request)
        self.assertEqual(len(package.references), 1)
        self.assertEqual(package.references[0].data, photo)
        self.assertNotIn(base64.b64encode(photo).decode()[:40], package.full_prompt)
        self.assertEqual([p['image']['reference_image'] for p in package.sections['4_stock']['products']], [1, 1])
        self.assertIn('참고 이미지 1:', package.user_prompt)
        product['image']['data'] = base64.b64encode(b'not an image').decode()
        with self.assertRaises(ValueError) as caught:
            build_image_input(request)
        self.assertEqual(caught.exception.code, 'IMAGE_REFERENCE_INVALID')

    def test_only_the_first_three_trends_are_carried(self):
        request = image_request(fixture('integrated-ok'))
        draft, trend, stock = inner(request)
        base = trend['trends'][0]
        evidence0 = trend['evidence'][0]
        trend['trends'], trend['evidence'] = [], []
        stock['trend_results'], products = [], stock['products']
        stock['products'] = []
        draft['draft']['claims'] = [draft['draft']['claims'][0]]
        for index in range(5):
            entry = copy.deepcopy(base)
            entry['style'].update(id=f'style-{index}', name=f'트렌드 {index}', article_id=f'art-{index}',
                                  focus_evidence_ids=[f'trend:art-{index}:0'], description=[])
            for item in entry['items']:
                item['evidence_ids'] = [f'trend:art-{index}:0']
            trend['trends'].append(entry)
            trend['evidence'].append(dict(evidence0, evidence_id=f'trend:art-{index}:0', article_id=f'art-{index}', quote=f'인용 {index}'))
            stock['products'].append(copy.deepcopy(products[0]))
            stock['trend_results'].append(dict(style={'id': f'style-{index}', 'name': f'트렌드 {index}'}, status='ok', summary=f'요약 {index}',
                                               product_index=index, evidence_ids=[]))
            draft['draft']['claims'].append(dict(kind='fact', text=f'인용 {index}', evidence_refs=[{'evidence_id': f'trend:art-{index}:0'}]))
        package = build_image_input(request)
        self.assertEqual(MAX_TRENDS, 3)
        self.assertEqual([t['style']['id'] for t in package.sections['3_trend']['trends']], ['style-0', 'style-1', 'style-2'])
        self.assertEqual(package.sections['3_trend']['evidence_codes'], [f'trend:art-{i}:0' for i in range(3)])
        self.assertEqual([s['style']['id'] for s in package.sections['4_stock']['trend_results']], ['style-0', 'style-1', 'style-2'])
        self.assertEqual([p['product_index'] for p in package.sections['4_stock']['products']], [0, 1, 2])
        self.assertEqual([c['text'] for c in package.sections['2_draft']['claims'][1:]], ['인용 0', '인용 1', '인용 2'])
        self.assertIn('트렌드 5개 중 앞의 3개', package.sections['3_trend']['scope'])
        dropped = package.provenance['excluded']['trends_after_limit']
        self.assertEqual([(d['name'], d['evidence_codes']) for d in dropped], [('트렌드 3', ['trend:art-3:0']), ('트렌드 4', ['trend:art-4:0'])])
        self.assertEqual((package.stats['trends_kept'], package.stats['trends_total']), (3, 5))

    def test_a_minimal_payload_still_builds(self):
        package = build_image_input({'request_id': 'r-1', 'lineage': {}})
        self.assertEqual(list(package.sections), SECTION_ORDER)
        self.assertEqual(package.references, ())


class FakeResponse:
    def __init__(self, payload, request_id='req_123'):
        self.payload, self.headers = payload, {'x-request-id': request_id}

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def png_b64():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (8, 8), (10, 120, 60)).save(out, format='PNG')
    return base64.b64encode(out.getvalue()).decode()


USAGE = {'input_tokens': 4200, 'input_tokens_details': {'text_tokens': 4100, 'image_tokens': 100}, 'output_tokens': 6000}


class ProviderTests(unittest.TestCase):
    def capture(self, payload=None):
        sent = []

        def urlopen(request, timeout):
            sent.append((request.full_url, json.loads(request.data), dict(request.header_items()), timeout))
            return FakeResponse(payload or {'data': [{'b64_json': png_b64()}], 'usage': USAGE})
        return sent, urlopen

    def test_reference_photos_go_through_images_edits_as_data_urls(self):
        request = image_request(fixture('integrated-ok'))
        _, _, stock = inner(request)
        stock['products'][0]['image'] = dict(media_type='image/jpeg', encoding='base64', data=base64.b64encode(jpeg_bytes()).decode())
        fields = build_image_input(request).provider_fields()
        sent, urlopen = self.capture()
        with environment(VOGUE_WORKFLOW_IMAGES_ENABLED='1', OPENAI_API_KEY='dummy-not-a-key'), \
                patch('urllib.request.urlopen', urlopen):
            result = workflow_http.final_image(dict(request, image_input=fields))
        url, body, headers, _ = sent[0]
        self.assertTrue(url.endswith('/v1/images/edits'))
        self.assertEqual((body['size'], body['quality'], body['output_format'], body['n']), ('1536x1024', 'high', 'png', 1))
        self.assertTrue(body['images'][0]['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(body['prompt'], compose_full_prompt(fields['system_prompt'], fields['user_prompt']))
        self.assertEqual(result[1], 'image/png')
        self.assertEqual(result.meta['endpoint'], 'images/edits')
        self.assertEqual(result.meta['usage'], USAGE)
        self.assertEqual(result.meta['provider_request_id'], 'req_123')
        self.assertNotIn('prompt_ignored', result.meta)

    def test_without_photos_generations_gets_explicit_output_options(self):
        request = image_request(fixture('integrated-ok'))
        sent, urlopen = self.capture()
        with environment(VOGUE_WORKFLOW_IMAGES_ENABLED='1', OPENAI_API_KEY='dummy-not-a-key'), \
                patch('urllib.request.urlopen', urlopen):
            result = workflow_http.final_image(request)
        url, body, _, _ = sent[0]
        self.assertTrue(url.endswith('/v1/images/generations'))
        self.assertNotIn('images', body)
        self.assertEqual((body['size'], body['quality'], body['output_format']), ('1536x1024', 'high', 'png'))
        self.assertLess(len(body['prompt']), IMAGE_PROMPT_MAX_CHARS)
        self.assertEqual(result.meta['endpoint'], 'images/generations')

    def test_error_body_code_and_request_id_are_kept_but_not_the_message(self):
        headers = Message()
        headers['x-request-id'] = 'req_abc'
        body = json.dumps({'error': {'code': 'string_above_max_length', 'type': 'invalid_request_error', 'param': 'prompt',
                                     'message': 'Invalid prompt: SECRET-LOOKING-INPUT-ECHO'}}).encode()

        def urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, 'Bad Request', headers, io.BytesIO(body))
        provider = OpenAIImageProvider(api_key='dummy-not-a-key')
        from vogue_orchestrator.workflows.image_generation import ImageGenerationRequest
        request = ImageGenerationRequest(request_id='r', season=None, category=None, prompt='p', prompt_version='v', prompt_sha256='0' * 64,
                                         evidence_ids=(), proposal_fields=(), limitations=(), conflicts=(), system_prompt='s')
        with patch('urllib.request.urlopen', urlopen), self.assertRaises(ImageProviderError) as caught:
            provider.generate(request)
        self.assertEqual(caught.exception.code, 'IMAGE_API_HTTP_400:string_above_max_length:prompt')
        self.assertEqual(caught.exception.meta, {'http_status': 400, 'provider_request_id': 'req_abc', 'error_code': 'string_above_max_length',
                                                 'error_type': 'invalid_request_error', 'error_param': 'prompt'})
        self.assertNotIn('SECRET', json.dumps(caught.exception.meta))

    def test_an_over_long_prompt_is_refused_before_any_http_call(self):
        def urlopen(request, timeout):
            raise AssertionError('no HTTP call for an over-long prompt')
        with patch('urllib.request.urlopen', urlopen):
            with self.assertRaises(ImageProviderError) as caught:
                OpenAIImageEditProvider(api_key='k').generate_from_reference('x' * (IMAGE_PROMPT_MAX_CHARS + 1), (jpeg_bytes(), 'image/jpeg'))
            self.assertEqual(caught.exception.code, 'IMAGE_PROMPT_TOO_LONG')
            from vogue_orchestrator.workflows.image_generation import ImageGenerationRequest
            request = ImageGenerationRequest(request_id='r', season=None, category=None, prompt='x' * IMAGE_PROMPT_MAX_CHARS, prompt_version='v',
                                             prompt_sha256='0' * 64, evidence_ids=(), proposal_fields=(), limitations=(), conflicts=(), system_prompt='s')
            with self.assertRaises(ImageProviderError) as caught:
                OpenAIImageProvider(api_key='k').generate(request)
            self.assertEqual(caught.exception.code, 'IMAGE_PROMPT_TOO_LONG')

    def test_a_prompt_the_provider_ignored_is_flagged(self):
        request = image_request(fixture('integrated-ok'))
        ignored = dict(USAGE, input_tokens_details={'text_tokens': 8, 'image_tokens': 0})
        sent, urlopen = self.capture({'data': [{'b64_json': png_b64()}], 'usage': ignored})
        with environment(VOGUE_WORKFLOW_IMAGES_ENABLED='1', OPENAI_API_KEY='dummy-not-a-key'), \
                patch('urllib.request.urlopen', urlopen):
            result = workflow_http.final_image(request)
        self.assertTrue(result.meta['prompt_ignored'])


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'Install the workflow extra.')
class WorkflowImageInputTests(unittest.TestCase):
    """The state machine hands the package to the provider and keeps diagnostics in the checkpoint only."""

    def setUp(self):
        from vogue_orchestrator.workflow import Workflow, WireResponse
        from vogue_orchestrator.workflow_contract import WorkflowContract
        from vogue_orchestrator.workflow_store import WorkflowStore, digest
        fixtures = Path(__file__).parent / 'fixtures/workflow'
        self.tmp = tempfile.TemporaryDirectory(prefix='p08-image-input-')
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(os.path.realpath(self.tmp.name))
        self.sample = json.loads((fixtures / 'draft-request-text-ok.json').read_bytes())
        self.rid = self.sample['request_id']
        self.image_data = next((fixtures / 'raw').glob('*.png')).read_bytes()
        self.received = []
        self.image_meta = {}

        def call(stage, payload):
            if stage in {'trend', 'stock'}:
                value = copy.deepcopy(self.sample['input'][stage + '_response']['raw_response'])
            else:
                value = json.loads((fixtures / ('draft-response-ok.json' if stage == 'planning_draft' else 'review-response-ok.json')).read_bytes())
                if stage == 'planning_draft':
                    value['result']['source_request'] = copy.deepcopy(payload)
                    value['result']['source_request_sha256'] = digest(payload)
                else:
                    source = payload['input']['planning_draft']
                    value['result']['review']['draft_response_sha256'] = source['content_sha256']
                    value['result']['review']['reviewed_draft'] = source['raw_response']['result']['draft_identity']
                    value['result']['review_identity']['content_sha256'] = digest(value['result']['review'])
            return WireResponse(json.dumps(value).encode())

        def image(payload):
            self.received.append(payload)
            return workflow_http.ImageResult(self.image_data, 'image/png', self.image_meta)
        self.workflow = Workflow(trend=lambda p: call('trend', p), stock=lambda p: call('stock', p),
                                 draft=lambda p: call('planning_draft', p), review=lambda p: call('sales_market_review', p),
                                 image=image, store=self.store, contract=WorkflowContract())

    def run_flow(self):
        request = self.sample['input']
        return self.workflow.run(request_id=self.rid, user_input=request['user_input'], planning_request=request['planning_request'],
                                 trend_query=dict(request_id=self.rid, original_query=request['user_input']['text']))

    def done_checkpoint(self):
        with self.store.db() as db:
            rows = db.execute("SELECT key, value FROM checkpoints WHERE state='done'").fetchall()
        return {key: json.loads(value) for key, value in rows}

    def test_the_provider_gets_the_package_and_the_checkpoint_keeps_the_diagnostics(self):
        self.image_meta = {'endpoint': 'images/generations', 'usage': USAGE, 'provider_request_id': 'req_1'}
        result = self.run_flow()['envelope']['result']
        self.assertEqual(result['final_image']['status'], 'generated')
        self.assertEqual(set(result['final_image']), {'status', 'artifact', 'lineage', 'reason', 'provider_calls'})
        fields = self.received[0]['image_input']
        self.assertEqual(fields['prompt_version'], 'p08-reviewed-image/2')
        self.assertLess(fields['full_prompt_chars'], IMAGE_PROMPT_MAX_CHARS)
        checkpoints = self.done_checkpoint()
        done = next(value for key, value in checkpoints.items() if isinstance(value, dict) and value.get('status') == 'generated')
        self.assertEqual(done['provider_meta']['usage'], USAGE)
        self.assertEqual(done['provider_meta']['prompt_chars'], fields['full_prompt_chars'])
        prompt_record = next(value for key, value in checkpoints.items() if key.endswith(':prompt'))
        record = json.loads(self.store.verify(prompt_record['prompt_artifact']))
        self.assertEqual(len(record['full_prompt']), fields['full_prompt_chars'])
        self.assertEqual(self.run_flow()['envelope']['result']['final_image'], result['final_image'])  # replay: no extra keys
        self.assertEqual(len(self.received), 1)

    def test_an_ignored_prompt_fails_the_image_and_keeps_the_bytes_private(self):
        self.image_meta = {'prompt_ignored': True, 'usage': dict(USAGE, input_tokens_details={'text_tokens': 8})}
        result = self.run_flow()['envelope']['result']
        self.assertEqual(result['final_image']['status'], 'failed')
        self.assertIsNone(result['final_image']['artifact'])
        self.assertIn('Provider error: IMAGE_PROMPT_IGNORED.', result['errors'][-1]['message'])
        done = next(value for value in self.done_checkpoint().values() if isinstance(value, dict) and value.get('status') == 'failed')
        self.assertEqual(self.store.verify(done['provider_meta']['ignored_image_artifact']), self.image_data)


if __name__ == '__main__':
    unittest.main()
