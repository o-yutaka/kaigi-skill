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
TOKEN = "a" * 64


class Handler(BaseHTTPRequestHandler):
    messages = []

    def sendj(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
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
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        if self.path.startswith("/api/status"):
            self.sendj({"alpha": {"available": True, "role": ""}})
            return
        if self.path.startswith("/api/messages"):
            self.sendj(list(self.messages))
            return
        self.sendj({"error": "notfound"}, 404)

    def do_POST(self):
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/send":
            msg = {"id": len(self.messages) + 1, "sender": "user", "text": payload["text"], "channel": payload["channel"]}
            self.messages.append(msg)
            self.sendj(msg)
            return
        self.sendj({"error": "notfound"}, 404)

    def log_message(self, *_):
        pass


class AuthBootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.home = root / "agentchattr"
        cls.home.mkdir()
        cls.state = root / "state"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def env(self):
        env = os.environ.copy()
        for key in ("KAIGI_BEARER_TOKEN", "AGENTCHATTR_AGENT_TOKEN", "KAIGI_TOKEN", "AGENTCHATTR_TOKEN"):
            env.pop(key, None)
        env.update({
            "AGENTCHATTR_SERVER": f"http://127.0.0.1:{self.port}",
            "AGENTCHATTR_HOME": str(self.home),
            "KAIGI_STATE_DIR": str(self.state),
            "NO_COLOR": "1",
        })
        return env

    def test_public_cli_bootstraps_current_loopback_session_token(self):
        Handler.messages = []
        r = subprocess.run([str(CLI), "say", "auth", "works"], env=self.env(), text=True, capture_output=True, timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Handler.messages[-1]["text"], "auth works")

    def test_status_reports_server_root_auth_source(self):
        r = subprocess.run([str(CLI), "status"], env=self.env(), text=True, capture_output=True, timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("server-root", r.stdout)

    def test_explicit_session_token_has_precedence(self):
        Handler.messages = []
        env = self.env()
        env["KAIGI_TOKEN"] = "wrong-explicit-token"
        r = subprocess.run([str(CLI), "say", "must", "fail"], env=env, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Handler.messages, [])


if __name__ == "__main__":
    unittest.main()
