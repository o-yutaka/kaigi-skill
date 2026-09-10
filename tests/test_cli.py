import json
import os
import pathlib
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "kaigi"


class Handler(BaseHTTPRequestHandler):
    messages = [{"id": 1, "sender": "claude", "text": "ready", "channel": "general", "timestamp": time.time()}]

    def authed(self):
        return self.headers.get("X-Session-Token") == "testtoken"

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        p = urlparse(self.path)
        if p.path == "/":
            self.send_response(200); self.end_headers(); return
        if p.path != "/api/messages" or not self.authed():
            self.send_response(403); self.end_headers(); return
        q = parse_qs(p.query)
        out = list(self.messages)
        if "since_id" in q:
            sid = int(q["since_id"][0]); out = [m for m in out if m["id"] > sid]
        if "limit" in q:
            out = out[-int(q["limit"][0]):]
        body = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/send" or not self.authed():
            self.send_response(403); self.end_headers(); return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n))
        msg = {"id": len(self.messages) + 1, "sender": "user", "text": payload["text"], "channel": payload["channel"], "timestamp": time.time()}
        self.messages.append(msg)
        body = json.dumps(msg).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_):
        pass


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.env = os.environ.copy()
        cls.env.update({"AGENTCHATTR_SERVER": f"http://127.0.0.1:{cls.port}", "KAIGI_TOKEN": "testtoken", "NO_COLOR": "1", "AGENTCHATTR_HOME": "/tmp/kaigi-test-missing-home"})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def run_cli(self, *args, input_text=None):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, input=input_text, capture_output=True, timeout=5)

    def test_status_and_log(self):
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("API OK", r.stdout)
        r = self.run_cli("log", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude: ready", r.stdout)

    def test_say_and_shortcut(self):
        r = self.run_cli("say", "hello", "world")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("@claude", "check", "this")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Handler.messages[-1]["text"], "@claude check this")

    def test_room_can_exit_noninteractive(self):
        r = self.run_cli("room", "--tail", "2", input_text="/quit\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("you ›", r.stdout)


if __name__ == "__main__":
    unittest.main()
