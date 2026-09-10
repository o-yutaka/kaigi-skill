from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "kaigi"
TOKEN = "b" * 64


class Handler(BaseHTTPRequestHandler):
    messages: list[dict] = []

    def _json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("X-Session-Token") == TOKEN

    def do_GET(self):
        if self.path == "/":
            body = f'<html><head><script>window.__SESSION_TOKEN__="{TOKEN}";</script></head></html>'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._authed():
            self._json({"error": "auth"}, 403)
            return
        if self.path.startswith("/api/status"):
            self._json({"alpha": {"available": True, "role": ""}})
            return
        if self.path.startswith("/api/messages"):
            self._json(list(self.messages))
            return
        self._json({"error": "notfound"}, 404)

    def do_POST(self):
        if not self._authed():
            self._json({"error": "auth"}, 403)
            return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/send":
            msg = {
                "id": len(self.messages) + 1,
                "sender": "user",
                "text": payload["text"],
                "channel": payload["channel"],
            }
            self.messages.append(msg)
            self._json(msg)
            return
        self._json({"error": "notfound"}, 404)

    def log_message(self, *_args):
        pass


class PublicCliAuthE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.agent_home = root / "agentchattr"
        cls.agent_home.mkdir()
        cls.state = root / "state"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _env(self):
        env = os.environ.copy()
        for key in ("KAIGI_BEARER_TOKEN", "AGENTCHATTR_AGENT_TOKEN", "KAIGI_TOKEN", "AGENTCHATTR_TOKEN"):
            env.pop(key, None)
        env.update({
            "AGENTCHATTR_SERVER": self.url,
            "AGENTCHATTR_HOME": str(self.agent_home),
            "KAIGI_STATE_DIR": str(self.state),
            "NO_COLOR": "1",
        })
        return env

    def setUp(self):
        Handler.messages = []

    def test_no_auth_env_public_say_uses_live_loopback_session(self):
        result = subprocess.run(
            [str(CLI), "say", "relay", "auth", "works"],
            env=self._env(), text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([m["text"] for m in Handler.messages], ["relay auth works"])

    def test_public_status_observes_live_auth_source(self):
        result = subprocess.run(
            [str(CLI), "status"], env=self._env(), text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local-index-session", result.stdout)

    def test_explicit_session_token_is_not_silently_overridden(self):
        env = self._env()
        env["KAIGI_TOKEN"] = "deliberately-wrong-explicit-token"
        result = subprocess.run(
            [str(CLI), "say", "must", "fail"], env=env, text=True, capture_output=True, timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(Handler.messages, [])


if __name__ == "__main__":
    unittest.main()
