"""Artifact naming and resolution (conventions section 8): content-addressed P08 store, peer reader."""
import hashlib
import io
import os
import threading
import unittest
import uuid
import importlib.util

from _http_support import LiveServerMixin, environment, real_tempdir
from vogue_orchestrator import workflow_http
from vogue_orchestrator.workflow_store import (ArtifactError, PeerArtifactReader, WorkflowStore, artifact_path,
                                               canonical)

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment dependent
    Image = None


def png(color=(10, 20, 30)):
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(buffer, format='PNG')
    return buffer.getvalue()


def reference(data, uri=None, **overrides):
    sha = hashlib.sha256(data).hexdigest()
    value = dict(artifact_id='p07-' + sha[:16], uri=uri or 'artifact://p07/reviews/%s.json' % sha, sha256=sha,
                 media_type='application/json', size_bytes=len(data), immutable=True)
    value.update(overrides)
    return value


class ContentAddressedStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = real_tempdir(self)
        self.store = WorkflowStore(self.root / 'store')

    def code(self, call, *args, **kwargs):
        with self.assertRaises(ArtifactError) as raised:
            call(*args, **kwargs)
        return raised.exception.code

    def test_put_is_content_addressed_and_idempotent(self):
        data = b'{"synthetic":true}'
        sha = hashlib.sha256(data).hexdigest()
        first = self.store.put(data)
        self.assertEqual(first, dict(artifact_id=sha, uri='artifact://p08/blobs/' + sha, sha256=sha,
                                     media_type='application/json', size_bytes=len(data), immutable=True))
        self.assertEqual(self.store.put(data), first)
        self.assertEqual((self.store.blobs / sha).read_bytes(), data)
        self.assertEqual(sorted(p.name for p in self.store.blobs.iterdir()), [sha])  # no temp files left
        self.assertEqual(self.store.media_types(sha), [('application/json', len(data))])
        self.assertEqual(artifact_path(first['uri']), ('p08', ['blobs', sha]))

    def test_same_bytes_with_another_media_type_share_the_uri(self):
        data = b'{"error":"denied"}'
        as_json, as_text = self.store.put(data), self.store.put(data, 'text/plain')
        self.assertEqual(as_json['uri'], as_text['uri'])
        self.assertEqual((self.store.verify(as_json), self.store.verify(as_text)), (data, data))
        self.assertEqual(self.code(self.store.verify, dict(as_json, media_type='text/html')), 'ARTIFACT_REFERENCE_INVALID')

    @unittest.skipIf(Image is None, 'Pillow (workflow extra) required')
    def test_upload_owners_are_tracked_per_request(self):
        image = png()
        first, second = self.store.upload(image, 'image/png', 'req-a'), self.store.upload(image, 'image/png', 'req-b')
        self.assertEqual(first, second)
        self.assertEqual(self.store.verify(first, owner='req-a'), image)
        self.assertEqual(self.store.verify(first, owner='req-b'), image)
        self.assertEqual(self.code(self.store.verify, first, owner='req-c'), 'ARTIFACT_REFERENCE_INVALID')

    def test_legacy_local_rows_still_verify(self):
        data = b'{"legacy":1}'
        sha = hashlib.sha256(data).hexdigest()
        (self.store.blobs / sha).write_bytes(data)
        legacy_id = uuid.uuid4().hex
        legacy = dict(artifact_id=legacy_id, uri='artifact://local/' + legacy_id, sha256=sha,
                      media_type='application/json', size_bytes=len(data), immutable=True)
        with self.store.db() as db:
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?)', (legacy['uri'], canonical(legacy).decode(), sha, 'req-1'))
        self.assertEqual(self.store.verify(legacy), data)
        self.assertEqual(self.store.verify(legacy, owner='req-1'), data)
        self.assertEqual(self.code(self.store.verify, legacy, owner='req-2'), 'ARTIFACT_REFERENCE_INVALID')

    def test_damaged_blobs_fail_closed_and_put_heals_them(self):
        data = b'0123456789'
        artifact = self.store.put(data)
        blob = self.store.blobs / artifact['sha256']
        blob.write_bytes(b'0123456780')
        self.assertEqual(self.code(self.store.verify, artifact), 'ARTIFACT_HASH_MISMATCH')
        blob.write_bytes(b'01234')
        self.assertEqual(self.code(self.store.verify, artifact), 'ARTIFACT_SIZE_MISMATCH')
        self.assertEqual(self.store.put(data), artifact)
        self.assertEqual(self.store.verify(artifact), data)
        blob.unlink()
        self.assertEqual(self.code(self.store.verify, artifact), 'ARTIFACT_UNAVAILABLE')
        outside = self.root / 'outside'
        outside.write_bytes(data)
        blob.symlink_to(outside)
        self.assertEqual(self.code(self.store.verify, artifact), 'INVALID_ARTIFACT_PATH')
        self.assertEqual(self.code(self.store.put, data), 'ARTIFACT_STORAGE_CORRUPT')

    def test_concurrent_identical_puts_agree(self):
        data = os.urandom(1 << 20)
        results, errors = [], []

        def put():
            try:
                results.append(WorkflowStore(self.root / 'store').put(data))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=put) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({canonical(r) for r in results}), 1)
        self.assertEqual(self.store.verify(results[0]), data)
        self.assertEqual(len(list(self.store.blobs.iterdir())), 1)

    def test_store_root_follows_the_environment(self):
        self.assertEqual(workflow_http.store_root({}), workflow_http.ROOT / '.local' / 'workflow')
        self.assertEqual(workflow_http.store_root({'VOGUE_WORKFLOW_STORE_DIR': str(self.root / 'x')}), self.root / 'x')
        self.assertEqual(workflow_http.store_root({'VOGUE_WORKFLOW_STORE_DIR': 'rel/store'}), workflow_http.ROOT / 'rel' / 'store')
        with environment(VOGUE_WORKFLOW_STORE_DIR=str(self.root / 'env-store')):
            private = workflow_http.store()
        self.assertEqual(private.root, self.root / 'env-store')
        self.assertTrue((self.root / 'env-store' / 'blobs').is_dir())


class PeerArtifactReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = real_tempdir(self) / 'peer'
        (self.root / 'p07' / 'reviews').mkdir(parents=True)
        self.reader = PeerArtifactReader(self.root)
        self.data = b'{"review":"synthetic"}'
        self.reference = reference(self.data)
        self.path = self.root / 'p07' / 'reviews' / (self.reference['sha256'] + '.json')
        self.path.write_bytes(self.data)

    def code(self, value):
        with self.assertRaises(ArtifactError) as raised:
            self.reader(value)
        return raised.exception.code, raised.exception.http_status

    def test_resolves_a_verified_peer_artifact(self):
        self.assertEqual(self.reader(self.reference), self.data)

    def test_uri_grammar_namespace_and_escapes(self):
        cases = {'file:///etc/passwd': 'INVALID_ARTIFACT_URI', 'artifact://p07/a b.json': 'INVALID_ARTIFACT_URI',
                 'artifact://p08/blobs/' + 'a' * 64: 'INVALID_ARTIFACT_URI', 'artifact://fixtures/raw/x': 'INVALID_ARTIFACT_URI',
                 'artifact://p07/../etc/passwd': 'INVALID_ARTIFACT_PATH', 'artifact://p07//x': 'INVALID_ARTIFACT_PATH',
                 'artifact://p07/./reviews/x': 'INVALID_ARTIFACT_PATH', 'artifact://p07': 'INVALID_ARTIFACT_PATH'}
        for uri, code in cases.items():
            with self.subTest(uri=uri):
                self.assertEqual(self.code(dict(self.reference, uri=uri)), (code, 422))

    def test_symlinks_are_refused_at_every_level(self):
        other = self.root.parent / 'elsewhere'
        (other / 'reviews').mkdir(parents=True)
        (other / 'reviews' / self.path.name).write_bytes(self.data)
        (self.root / 'p07' / 'linked').symlink_to(other / 'reviews', target_is_directory=True)
        linked = dict(self.reference, uri='artifact://p07/linked/' + self.path.name)
        self.assertEqual(self.code(linked), ('INVALID_ARTIFACT_PATH', 422))
        (self.root / 'p07' / 'reviews' / 'file-link.json').symlink_to(self.path)
        self.assertEqual(self.code(dict(self.reference, uri='artifact://p07/reviews/file-link.json')),
                         ('INVALID_ARTIFACT_PATH', 422))

    def test_missing_mismatched_and_irregular_files_fail_closed(self):
        self.assertEqual(self.code(dict(self.reference, uri='artifact://p07/reviews/absent.json')), ('ARTIFACT_UNAVAILABLE', 503))
        with self.assertRaises(ArtifactError) as raised:
            PeerArtifactReader(self.root / 'absent-root')(self.reference)
        self.assertEqual(raised.exception.code, 'ARTIFACT_UNAVAILABLE')
        self.assertEqual(self.code(dict(self.reference, size_bytes=len(self.data) + 1)), ('ARTIFACT_SIZE_MISMATCH', 422))
        self.assertEqual(self.code(dict(self.reference, sha256='0' * 64)), ('ARTIFACT_HASH_MISMATCH', 422))
        (self.root / 'p07' / 'reviews' / 'dir.json').mkdir()
        self.assertEqual(self.code(dict(self.reference, uri='artifact://p07/reviews/dir.json')), ('INVALID_ARTIFACT_PATH', 422))
        os.mkfifo(self.root / 'p07' / 'reviews' / 'fifo.json')  # must not block
        self.assertEqual(self.code(dict(self.reference, uri='artifact://p07/reviews/fifo.json')), ('INVALID_ARTIFACT_PATH', 422))
        self.assertEqual(self.code(dict(self.reference, size_bytes=16777217)), ('ARTIFACT_TOO_LARGE', 413))
        self.assertEqual(self.code(dict(self.reference, size_bytes='22')), ('ARTIFACT_REFERENCE_INVALID', 422))

    def test_import_verified_keeps_the_peer_uri(self):
        store = WorkflowStore(self.root.parent / 'store')
        store.import_verified(self.reference, self.reader(self.reference))
        self.assertEqual(store.verify(self.reference), self.data)
        with self.assertRaises(ValueError):
            store.import_verified(dict(self.reference, artifact_id='p07-other'), self.data)

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'workflow extra (jsonschema) required')
    def test_build_workflow_wires_the_reader_from_the_environment(self):
        self.assertIsNone(workflow_http.peer_artifact_reader({}))
        reader = workflow_http.peer_artifact_reader({'VOGUE_PEER_ARTIFACT_ROOT': str(self.root)})
        self.assertEqual(reader(self.reference), self.data)
        env = dict(VOGUE_WORKFLOW_ALLOW_PROPOSED='1', VOGUE_AGENT_HTTP_DEV_MODE='1', VOGUE_PEER_ARTIFACT_ROOT=str(self.root),
                   VOGUE_WORKFLOW_STORE_DIR=str(self.root.parent / 'wf'),
                   **{k: 'http://127.0.0.1:9' for k in ('VOGUE_TREND_AGENT_URL', 'VOGUE_STOCK_AGENT_URL',
                                                       'VOGUE_NEW_PRODUCT_AGENT_URL', 'VOGUE_SALES_MARKET_AGENT_URL')})
        with environment(**env):
            self.assertEqual(workflow_http.build_workflow().artifact_reader.root, self.root)
            self.assertIsNone(workflow_http.build_workflow(artifact_reader=None).artifact_reader)
            custom = object()
            self.assertIs(workflow_http.build_workflow(artifact_reader=custom).artifact_reader, custom)


class ArtifactRouteTests(LiveServerMixin, unittest.TestCase):
    def setUp(self):
        self.start_server()
        self.store_dir = self.state / 'workflow'
        self.store = WorkflowStore(self.store_dir)

    def get(self, artifact_id):
        with environment(VOGUE_WORKFLOW_STORE_DIR=str(self.store_dir)):
            return self.request('GET', '/api/workflow/artifacts/' + artifact_id)

    @unittest.skipIf(Image is None, 'Pillow (workflow extra) required')
    def test_serves_images_json_and_quarantines_other_media(self):
        image = png()
        status, body, response = self.get(self.store.put(image, 'image/png')['artifact_id'])
        self.assertEqual((status, body, response.getheader('Content-Type')), (200, image, 'image/png'))
        status, body, response = self.get(self.store.put(b'{"a":1}')['artifact_id'])
        self.assertEqual((status, body), (200, {'a': 1}))
        html = self.store.put(b'<script>alert(1)</script>', 'text/html')
        status, body, response = self.get(html['artifact_id'])
        self.assertEqual((status, response.getheader('Content-Type')), (200, 'application/octet-stream'))
        self.assertTrue(response.getheader('Content-Disposition').startswith('attachment'))

    def test_unknown_invalid_and_damaged_artifacts(self):
        status, body, _ = self.get('f' * 64)
        self.assertEqual((status, body['error']['code']), (404, 'ARTIFACT_NOT_FOUND'))
        self.assertEqual(self.get('not-an-id')[0], 422)
        artifact = self.store.put(b'0123456789')
        (self.store.blobs / artifact['sha256']).write_bytes(b'0123456780')
        status, body, _ = self.get(artifact['artifact_id'])
        self.assertEqual((status, body['error']['code']), (422, 'ARTIFACT_HASH_MISMATCH'))

    def test_legacy_ids_are_still_served(self):
        data = b'{"legacy":true}'
        sha = hashlib.sha256(data).hexdigest()
        (self.store.blobs / sha).write_bytes(data)
        legacy_id = uuid.uuid4().hex
        legacy = dict(artifact_id=legacy_id, uri='artifact://local/' + legacy_id, sha256=sha,
                      media_type='application/json', size_bytes=len(data), immutable=True)
        with self.store.db() as db:
            db.execute('INSERT INTO artifacts VALUES (?,?,?,NULL)', (legacy['uri'], canonical(legacy).decode(), sha))
        self.assertEqual(self.get(legacy_id)[:2], (200, {'legacy': True}))

    @unittest.skipIf(Image is None, 'Pillow (workflow extra) required')
    def test_upload_returns_the_same_reference_for_the_same_bytes(self):
        image = png((1, 2, 3))
        with environment(VOGUE_WORKFLOW_STORE_DIR=str(self.store_dir)):
            first = self.request('POST', '/v1/input-images', raw=image, content_type='image/png', **{'X-Request-ID': 'req-1'})
            second = self.request('POST', '/v1/input-images', raw=image, content_type='image/png', **{'X-Request-ID': 'req-2'})
        self.assertEqual((first[0], second[0]), (201, 201))
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[1]['uri'], 'artifact://p08/blobs/' + hashlib.sha256(image).hexdigest())
        self.assertEqual(self.store.verify(first[1], owner='req-2'), image)


if __name__ == '__main__':
    unittest.main()
