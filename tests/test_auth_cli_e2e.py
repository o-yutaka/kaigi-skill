from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import struct
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "kaigi"
TOKEN = "b" * 64
BEARER = "c" * 64
MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _read_exact(stream, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            raise ConnectionError("short websocket frame")
        data.extend(chunk)
    return bytes(data)


def _read_client_frame(stream) -> tuple[int, bytes]:
    b1, b2 = _read_exact(stream, 2)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(stream, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(stream, 8))[0]
    mask = _read_exact(stream, 4) if masked else b""
    payload = _read_exact(stream, length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return opcode, payload


def _server_text_frame(value: dict) -> bytes:
    payload = json.dumps(value, separators=(",", ":")).encode()
    n = len(payload)
    if n <= 125:
        return bytes((0x81, n)) + payload
    if n <= 0xFFFF:
        return bytes((0x81, 126)) + struct.pack("!H", n) + payload
    return bytes((0x81, 127)) + struct.pack("!Q", n) + payload


class Handler(BaseHTTPRequestHandler):
    messages: list[dict] = []
    send_modes: list[str] = []

    def _json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _session_authed(self):
        return self.headers.get("X-Session-Token") == TOKEN

    def _accept_ws_message(self, parsed: urllib.parse.SplitResult):
        supplied = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        if supplied != TOKEN:
            self._json({"error": "forbidden: invalid session token"}, 403)
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + MAGIC).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        opcode, payload = _read_client_frame(self.rfile)
        if opcode != 0x1:
            return
        event = json.loads(payload.decode())
        if event.get("type") != "message":
            return
        msg = {
            "id": len(self.messages) + 1,
            "sender": "user",
            "text": event["text"],
            "channel": event.get("channel", "general"),
        }
        self.messages.append(msg)
        self.send_modes.append("session-websocket")
        self.wfile.write(_server_text_frame({"type": "message", "data": msg}))
        self.wfile.flush()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            body = f'<html><head><script>window.__SESSION_TOKEN__="{TOKEN}";</script></head></html>'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/ws":
            self._accept_ws_message(parsed)
            return
        if not self._session_authed():
            self._json({"error": "forbidden: invalid or missing session token"}, 403)
            return
        if parsed.path == "/api/status":
            self._json({"alpha": {"available": True, "role": ""}})
            return
        if parsed.path == "/api/messages":
            params = urllib.parse.parse_qs(parsed.query)
            since = int(params.get("since_id", ["0"])[0] or 0)
            self._json([m for m in self.messages if int(m["id"]) > since])
            return
        self._json({"error": "notfound"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/api/send":
            self._json({"error": "notfound"}, 404)
            return

        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {BEARER}":
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n) or b"{}")
            msg = {
                "id": len(self.messages) + 1,
                "sender": "registered-agent",
                "text": payload["text"],
                "channel": payload.get("channel", "general"),
            }
            self.messages.append(msg)
            self.send_modes.append("bearer-rest")
            self._json(msg)
            return

        if not self._session_authed():
            self._json({"error": "forbidden: invalid or missing session token"}, 403)
            return
        self._json({"error": "missing Authorization: Bearer <token>"}, 401)

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
        Handler.send_modes = []

    def test_no_auth_env_public_say_falls_back_to_live_session_websocket(self):
        result = subprocess.run(
            [str(CLI), "say", "relay", "auth", "works"],
            env=self._env(), text=True, capture_output=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([m["text"] for m in Handler.messages], ["relay auth works"])
        self.assertEqual(Handler.send_modes, ["session-websocket"])

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

    def test_explicit_registered_agent_bearer_stays_on_rest(self):
        env = self._env()
        env["KAIGI_BEARER_TOKEN"] = BEARER
        result = subprocess.run(
            [str(CLI), "say", "agent", "path"], env=env, text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([m["text"] for m in Handler.messages], ["agent path"])
        self.assertEqual(Handler.send_modes, ["bearer-rest"])


if __name__ == "__main__":
    unittest.main()
