import contextlib
import io
import os
import unittest
from unittest.mock import patch

from vogue_orchestrator.server import serve


class ServerConfigurationTests(unittest.TestCase):
    def test_remote_bind_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {
            "VOGUE_ORCHESTRATOR_HOST": "0.0.0.0",
            "VOGUE_ORCHESTRATOR_PORT": "8765",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "ALLOW_REMOTE_BIND"):
                serve()

    def test_container_bind_uses_explicit_host_port_and_closes_server(self):
        fake_server = unittest.mock.Mock()
        fake_server.serve_forever.side_effect = KeyboardInterrupt
        with patch.dict(os.environ, {
            "VOGUE_ORCHESTRATOR_HOST": "0.0.0.0",
            "VOGUE_ORCHESTRATOR_PORT": "9876",
            "VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND": "1",
        }, clear=True):
            with patch("vogue_orchestrator.server.ThreadingHTTPServer", return_value=fake_server) as server_factory:
                with contextlib.redirect_stdout(io.StringIO()):
                    serve()

        server_factory.assert_called_once()
        self.assertEqual(server_factory.call_args.args[0], ("0.0.0.0", 9876))
        fake_server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
