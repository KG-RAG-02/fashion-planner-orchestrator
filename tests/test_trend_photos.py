"""Trend article photos for the planning sheet (trend_photos.py): which URLs are fetched, how they are checked,
and that stage 06 stores them once so the planning-sheet route renders them without network access."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from fashion_planner_orchestrator.planning_sheet import trend_image_urls
from fashion_planner_orchestrator.trend_photos import (PhotoRejected, TrendPhotos, allowed_hosts, checkpoint_key, enabled,
                                             fetch_photo, host_allowed, load_trend_photos)
from fashion_planner_orchestrator.workflow_store import WorkflowStore

FIXTURES = Path(__file__).resolve().parents[1] / 'tools' / 'workflow_contract' / 'fixtures'
HOSTS = ('vogue.co.kr',)


def png(colour=(200, 30, 30)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (16, 16), colour).save(out, format='PNG')
    return out.getvalue()


def envelope_with_photos(urls, item_urls=()):
    envelope = copy.deepcopy(json.loads((FIXTURES / 'integrated-visuals-ok.json').read_text(encoding='utf-8')))
    trend = envelope['result']['sources']['trend']['raw_response']['result']['trends'][0]
    trend['style']['example_images'] = [{'url': url, 'basis': 'article'} for url in urls]
    trend['items'][0]['image_urls'] = list(item_urls)
    return envelope


class FakeGet:
    def __init__(self, responses):
        self.responses, self.calls = dict(responses), []

    def __call__(self, url, timeout):
        self.calls.append(url)
        return self.responses[url]


class SettingsTests(unittest.TestCase):
    def test_hosts_default_to_vogue_and_admit_subdomains(self):
        self.assertEqual(allowed_hosts({}), ('vogue.co.kr',))
        self.assertEqual(allowed_hosts({'VOGUE_TREND_PHOTO_HOSTS': ' Img.Example.com , cdn.test. '}), ('img.example.com', 'cdn.test'))
        self.assertTrue(host_allowed('img.vogue.co.kr', HOSTS))
        self.assertTrue(host_allowed('vogue.co.kr', HOSTS))
        self.assertFalse(host_allowed('evilvogue.co.kr', HOSTS))
        self.assertFalse(host_allowed('vogue.co.kr.evil.com', HOSTS))
        self.assertFalse(host_allowed(None, HOSTS))

    def test_enabled_unless_set_to_zero(self):
        self.assertTrue(enabled({}))
        self.assertTrue(enabled({'VOGUE_TREND_PHOTOS_ENABLED': '1'}))
        self.assertFalse(enabled({'VOGUE_TREND_PHOTOS_ENABLED': '0'}))

    def test_urls_come_from_the_first_style_then_its_items(self):
        envelope = envelope_with_photos(['https://img.vogue.co.kr/a.jpg', 'https://img.vogue.co.kr/a.jpg'],
                                        ['https://img.vogue.co.kr/b.jpg', ' ', 'https://img.vogue.co.kr/c.jpg'])
        self.assertEqual(trend_image_urls(envelope['result']),
                         ('https://img.vogue.co.kr/a.jpg', 'https://img.vogue.co.kr/b.jpg', 'https://img.vogue.co.kr/c.jpg'))


class FetchTests(unittest.TestCase):
    def test_an_allowlisted_https_image_is_returned(self):
        get = FakeGet({'https://img.vogue.co.kr/a.png': (200, {'content-type': 'image/png'}, png())})
        data, media = fetch_photo('https://img.vogue.co.kr/a.png', HOSTS, get=get)
        self.assertEqual((data, media), (png(), 'image/png'))

    def test_scheme_host_type_and_bytes_are_checked(self):
        cases = {
            'http://img.vogue.co.kr/a.png': 'PHOTO_SCHEME_NOT_HTTPS',
            'https://example.com/a.png': 'PHOTO_HOST_NOT_ALLOWED',
        }
        for url, code in cases.items():
            with self.subTest(url=url), self.assertRaises(PhotoRejected) as caught:
                fetch_photo(url, HOSTS, get=FakeGet({}))
            self.assertEqual(caught.exception.code, code)
        responses = {
            'https://vogue.co.kr/html': ((200, {'content-type': 'text/html'}, b'<html>'), 'PHOTO_MEDIA_UNSUPPORTED'),
            'https://vogue.co.kr/bad': ((200, {'content-type': 'image/png'}, b'not an image'), 'PHOTO_DECODE_INVALID'),
            'https://vogue.co.kr/gone': ((404, {}, b''), 'PHOTO_HTTP_STATUS'),
        }
        for url, (response, code) in responses.items():
            with self.subTest(url=url), self.assertRaises(PhotoRejected) as caught:
                fetch_photo(url, HOSTS, get=FakeGet({url: response}))
            self.assertEqual(caught.exception.code, code)

    def test_redirects_must_stay_on_the_allowlist(self):
        get = FakeGet({'https://vogue.co.kr/a': (302, {'location': '/b'}, b''),
                       'https://vogue.co.kr/b': (200, {'content-type': 'image/png'}, png())})
        self.assertEqual(fetch_photo('https://vogue.co.kr/a', HOSTS, get=get)[1], 'image/png')
        self.assertEqual(get.calls, ['https://vogue.co.kr/a', 'https://vogue.co.kr/b'])
        get = FakeGet({'https://vogue.co.kr/a': (302, {'location': 'https://example.com/x.png'}, b'')})
        with self.assertRaises(PhotoRejected) as caught:
            fetch_photo('https://vogue.co.kr/a', HOSTS, get=get)
        self.assertEqual(caught.exception.code, 'PHOTO_HOST_NOT_ALLOWED')
        loop = FakeGet({'https://vogue.co.kr/a': (302, {'location': '/a'}, b'')})
        with self.assertRaises(PhotoRejected) as caught:
            fetch_photo('https://vogue.co.kr/a', HOSTS, get=loop)
        self.assertEqual(caught.exception.code, 'PHOTO_TOO_MANY_REDIRECTS')

    def test_a_network_error_is_a_rejection(self):
        def broken(url, timeout):
            raise OSError('down')
        with self.assertRaises(PhotoRejected) as caught:
            fetch_photo('https://vogue.co.kr/a.png', HOSTS, get=broken)
        self.assertEqual(caught.exception.code, 'PHOTO_UNAVAILABLE')

    def test_a_malformed_url_is_a_rejection(self):
        for url in ('https://[img.vogue.co.kr/a.jpg', 'https://vogue.co.kr:port/a.jpg'):
            with self.assertRaises(PhotoRejected) as caught:
                fetch_photo(url, HOSTS, get=FakeGet({}))
            self.assertEqual(caught.exception.code, 'PHOTO_URL_INVALID')
        get = FakeGet({'https://vogue.co.kr/a.png': (302, {'location': 'https://[broken/'}, b'')})
        with self.assertRaises(PhotoRejected) as caught:
            fetch_photo('https://vogue.co.kr/a.png', HOSTS, get=get)
        self.assertEqual(caught.exception.code, 'PHOTO_URL_INVALID')

    def test_a_photo_over_the_pixel_cap_is_rejected(self):
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (3001, 2000), (20, 20, 20)).save(out, format='JPEG')  # small file, too many pixels
        get = FakeGet({'https://vogue.co.kr/big.jpg': (200, {'content-type': 'image/jpeg'}, out.getvalue())})
        with self.assertRaises(PhotoRejected) as caught:
            fetch_photo('https://vogue.co.kr/big.jpg', HOSTS, get=get)
        self.assertEqual(caught.exception.code, 'PHOTO_TOO_LARGE')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(Path(self.tmp.name).resolve())

    def tearDown(self):
        self.tmp.cleanup()

    def test_photos_are_fetched_once_kept_and_reloaded_without_network(self):
        urls = [f'https://img.vogue.co.kr/{n}.png' for n in range(5)]
        calls = []

        def fetch(url, hosts):
            calls.append(url)
            if url.endswith('1.png'):
                raise PhotoRejected('PHOTO_HTTP_STATUS')
            return png((10 * len(calls), 40, 40)), 'image/png'

        photos = TrendPhotos(self.store, {}, fetch=fetch)
        first = photos('run-1', envelope_with_photos(urls))
        self.assertEqual([url for _, url in first], [urls[0], urls[2], urls[3]])  # three kept, the failure skipped
        record = self.store.checkpoint(checkpoint_key('run-1'))[1]
        self.assertEqual(record['skipped'], [{'url': urls[1], 'reason': 'PHOTO_HTTP_STATUS'}])
        self.assertEqual(len(calls), 4)
        again = photos('run-1', envelope_with_photos(urls))  # a retried stage 06 reuses the stored photos
        self.assertEqual(len(calls), 4)
        self.assertEqual(again, first)
        self.assertEqual(load_trend_photos(self.store, 'run-1'), first)
        self.assertEqual(load_trend_photos(self.store, 'run-2'), [])

    def test_an_unexpected_failure_drops_only_that_photo(self):
        urls = [f'https://img.vogue.co.kr/{n}.png' for n in range(3)]

        def fetch(url, hosts):
            if url.endswith('0.png'):
                raise ValueError('unexpected')
            return png(), 'image/png'

        kept = TrendPhotos(self.store, {}, fetch=fetch)('run-4', envelope_with_photos(urls))
        self.assertEqual([url for _, url in kept], urls[1:])
        self.assertEqual(self.store.checkpoint(checkpoint_key('run-4'))[1]['skipped'],
                         [{'url': urls[0], 'reason': 'PHOTO_UNAVAILABLE'}])

    def test_a_run_without_photo_urls_records_nothing_to_show(self):
        photos = TrendPhotos(self.store, {}, fetch=lambda url, hosts: self.fail('nothing to fetch'))
        self.assertEqual(photos('run-3', envelope_with_photos([])), [])
        self.assertEqual(load_trend_photos(self.store, 'run-3'), [])


if __name__ == '__main__':
    unittest.main()
