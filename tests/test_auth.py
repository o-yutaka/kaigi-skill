from __future__ import annotations

import http.server
import os
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import kaigi_auth


TOKEN = "a" * 64


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            '<!doctype html><html><head>'
            f'<script>window.__SESSION_TOKEN__="{TOKEN}";</script>'
            '</head><body>agentchattr</body></html>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class LocalAuthDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_discovers_current_session_token_from_loopback_index(self):
        self.assertEqual(kaigi_auth.discover_local_session_token(self.url), TOKEN)

    def test_never_scrapes_non_loopback_server_for_auth(self):
        with mock.patch("kaigi_auth.urllib.request.urlopen") as urlopen:
            self.assertIsNone(kaigi_auth.discover_local_session_token("https://example.com"))
            urlopen.assert_not_called()

    def test_explicit_environment_auth_wins(self):
        core = SimpleNamespace(
            SERVER_URL=self.url,
            resolve_token=lambda: ("legacy", "server-log"),
            auth_headers=lambda: {"Accept": "application/json"},
        )
        with mock.patch.dict(os.environ, {"KAIGI_BEARER_TOKEN": "explicit-secret"}, clear=False):
            kaigi_auth.apply_core(core)
            self.assertEqual(core.resolve_token(), ("explicit-secret", "bearer-env"))

    def test_live_index_beats_legacy_log_fallback(self):
        core = SimpleNamespace(
            SERVER_URL=self.url,
            resolve_token=lambda: ("stale-token", "server-log"),
            auth_headers=lambda: {"Accept": "application/json"},
        )
        with mock.patch.dict(
            os.environ,
            {"KAIGI_BEARER_TOKEN": "", "AGENTCHATTR_AGENT_TOKEN": "", "KAIGI_TOKEN": "", "AGENTCHATTR_TOKEN": ""},
            clear=False,
        ):
            kaigi_auth.apply_core(core)
            self.assertEqual(core.resolve_token(), (TOKEN, "local-index-session"))
            self.assertEqual(core.LOCAL_AUTH_POLICY, "local-session-discovery-v2-dual-header")


if __name__ == "__main__":
    unittest.main()
