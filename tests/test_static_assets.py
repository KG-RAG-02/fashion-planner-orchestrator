"""Static UI assets (ES modules, fonts) and the response security headers."""
import unittest
from unittest.mock import patch

from _http_support import LiveServerMixin, environment, real_tempdir
from fashion_planner_orchestrator import server as server_module


class StaticAssetTests(LiveServerMixin, unittest.TestCase):
    def setUp(self):
        self.start_server()
        root = real_tempdir(self) / 'web'
        for relative, data in (('index.html', b'<!doctype html><title>t</title>'), ('js/app.js', b'export const a = 1;\n'),
                               ('js/lib.mjs', b'export {};\n'), ('css/app.css', b'body{}\n'),
                               ('fonts/ui.woff2', b'wOF2\x00\x01'), ('fonts/ui.woff', b'wOFF\x00\x01'),
                               ('img/logo.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>')):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(data)
        (root.parent / 'secret.txt').write_bytes(b'outside the web root')
        patcher = patch.object(server_module, 'WEB_ROOT', root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_module_scripts_styles_and_fonts_have_explicit_types(self):
        expected = {'/js/app.js': 'text/javascript; charset=utf-8', '/js/lib.mjs': 'text/javascript; charset=utf-8',
                    '/css/app.css': 'text/css; charset=utf-8', '/fonts/ui.woff2': 'font/woff2', '/fonts/ui.woff': 'font/woff',
                    '/img/logo.svg': 'image/svg+xml', '/': 'text/html; charset=utf-8'}
        for container in ('', 'container'):
            for path, kind in expected.items():
                with self.subTest(path=path, runtime=container or 'host'), environment(ORCHESTRATOR_RUNTIME=container):
                    status, _, response = self.request('GET', path, Host='evil.example')  # static files are never gated
                    self.assertEqual((status, response.getheader('Content-Type')), (200, kind))
                    self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')

    def test_paths_outside_the_web_root_are_not_served(self):
        with environment():
            for path in ('/../secret.txt', '/js/../../secret.txt', '/missing.js'):
                with self.subTest(path=path):
                    self.assertEqual(self.request('GET', path)[0], 404)

    def test_content_security_policy(self):
        with environment():
            for path in ('/', '/api/health', '/api/workflow/nope'):
                with self.subTest(path=path):
                    policy = self.request('GET', path)[2].getheader('Content-Security-Policy')
                    directives = dict(item.strip().split(' ', 1) for item in policy.split(';'))
                    self.assertEqual(directives, {
                        'default-src': "'self'", 'script-src': "'self'", 'style-src': "'self'", 'font-src': "'self'",
                        'img-src': "'self' data: blob:", 'connect-src': "'self'", 'object-src': "'none'",
                        'base-uri': "'none'", 'frame-ancestors': "'none'", 'form-action': "'self'"})


if __name__ == '__main__':
    unittest.main()
