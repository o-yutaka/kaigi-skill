import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "kaigi"


class V6Handler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages = []
    status = {
        "claude": {"available": True, "role": ""},
        "codex": {"available": True, "role": ""},
        "chatgpt": {"available": True, "role": ""},
    }

    def authed(self):
        return self.headers.get("X-Session-Token") == "testtoken"

    def sendj(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @classmethod
    def add(cls, sender, text, channel="general"):
        with cls.lock:
            cls.messages.append({
                "id": len(cls.messages) + 1,
                "sender": sender,
                "text": text,
                "channel": channel,
                "timestamp": time.time(),
            })
            return dict(cls.messages[-1])

    @staticmethod
    def targets(text):
        prefix = text.split("[KAIGI", 1)[0]
        return [tok[1:] for tok in prefix.split() if tok.startswith("@")]

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(200)
            self.end_headers()
            return
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        if parsed.path == "/api/status":
            self.sendj(self.status)
            return
        if parsed.path == "/api/messages":
            q = parse_qs(parsed.query)
            with self.lock:
                out = list(self.messages)
            if "since_id" in q:
                sid = int(q["since_id"][0])
                out = [m for m in out if m["id"] > sid]
            if "channel" in q:
                out = [m for m in out if m.get("channel") == q["channel"][0]]
            if "limit" in q:
                out = out[-int(q["limit"][0]):]
            self.sendj(out)
            return
        self.sendj({"error": "notfound"}, 404)

    def do_POST(self):
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path != "/api/send":
            self.sendj({"error": "notfound"}, 404)
            return
        msg = self.add("user", payload["text"], payload.get("channel", "general"))
        self.sendj(msg)
        text = payload["text"]
        channel = payload.get("channel", "general")
        targets = self.targets(text)
        if "KAIGI COUNCIL / ROUND 1" in text:
            threading.Timer(0.03, lambda: [self.add(a, f"{a} independent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / ROUND 2" in text:
            threading.Timer(0.03, lambda: [self.add(a, f"{a} dissent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / FINAL" in text and targets:
            threading.Timer(0.03, lambda: self.add(targets[0], "DECISION: verified ship", channel)).start()

    def log_message(self, *_):
        pass


class V6CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.home = root / "agentchattr"
        cls.home.mkdir()
        cls.state = root / "state"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), V6Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = os.environ.copy()
        cls.env.update({
            "AGENTCHATTR_SERVER": f"http://127.0.0.1:{cls.port}",
            "KAIGI_TOKEN": "testtoken",
            "NO_COLOR": "1",
            "AGENTCHATTR_HOME": str(cls.home),
            "KAIGI_STATE_DIR": str(cls.state),
        })
        (cls.home / "config.toml").write_text(
            """
[agents.claude]
command = "true"
label = "Claude"

[agents.codex]
command = "true"
label = "Codex"

[agents.chatgpt]
type = "api"
base_url = "https://api.openai.com/v1"
model = "gpt-test"
api_key_env = "OPENAI_API_KEY"
label = "ChatGPT"
""".strip() + "\n",
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        time.sleep(0.08)
        with V6Handler.lock:
            V6Handler.messages = []
        if self.state.exists():
            import shutil
            shutil.rmtree(self.state)
        self.state.mkdir(parents=True)

    def run_cli(self, *args, timeout=10):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, capture_output=True, timeout=timeout)

    def latest_run_id(self):
        return json.loads((self.state / "latest.json").read_text())["run_id"]

    def complete_safe_meeting(self):
        r = self.run_cli("ship", "safely", "--round-timeout", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.latest_run_id()

    def test_public_version_is_v7(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("7.0.0", r.stdout)

    def test_bare_topic_safe_auto_creates_verified_packet(self):
        rid = self.complete_safe_meeting()
        user_texts = [m["text"] for m in V6Handler.messages if m["sender"] == "user"]
        first = next(x for x in user_texts if "ROUND 1" in x)
        self.assertIn("@claude", first)
        self.assertIn("@codex", first)
        self.assertNotIn("@chatgpt", first)
        packet = self.state / "packets" / f"{rid}.json"
        self.assertTrue(packet.is_file())
        data = json.loads(packet.read_text())
        self.assertEqual(data["schema"], "kaigi.decision_packet.v1")
        self.assertFalse(data["authority"]["execution_authorized"])
        self.assertEqual(data["decision"]["text"], "DECISION: verified ship")
        verified = self.run_cli("verify", rid)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("PASS", verified.stdout)

    def test_allow_cloud_includes_online_cloud_agent(self):
        r = self.run_cli(
            "decide", "cloud", "review", "--allow-cloud", "--max-agents", "3",
            "--round-timeout", "1",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        first = next(m["text"] for m in V6Handler.messages if m["sender"] == "user" and "ROUND 1" in m["text"])
        self.assertIn("@chatgpt", first)

    def test_direct_mention_remains_direct_message(self):
        r = self.run_cli("@claude", "ping", "now")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(V6Handler.messages[-1]["text"], "@claude ping now")
        self.assertFalse(any("KAIGI COUNCIL" in m["text"] for m in V6Handler.messages))

    def test_decide_dry_run_has_no_side_effect_message(self):
        before = len(V6Handler.messages)
        r = self.run_cli("decide", "preview", "only", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("safe-auto", r.stdout)
        self.assertIn("excluded-cloud", r.stdout)
        self.assertEqual(len(V6Handler.messages), before)
        self.assertFalse((self.state / "latest.json").exists())

    def test_verify_detects_packet_tamper(self):
        rid = self.complete_safe_meeting()
        path = self.state / "packets" / f"{rid}.json"
        data = json.loads(path.read_text())
        data["decision"]["text"] = "tampered"
        path.write_text(json.dumps(data), encoding="utf-8")
        r = self.run_cli("verify", rid)
        self.assertEqual(r.returncode, 1)
        self.assertIn("packet_sha256 mismatch", r.stdout)

    def test_verify_live_detects_server_message_tamper(self):
        rid = self.complete_safe_meeting()
        with V6Handler.lock:
            final = next(m for m in reversed(V6Handler.messages) if m["sender"] != "user" and m["text"].startswith("DECISION:"))
            final["text"] = "DECISION: altered at source"
        r = self.run_cli("verify", rid, "--live")
        self.assertEqual(r.returncode, 1)
        self.assertIn("live transcript differs", r.stdout)

    def test_handoff_is_advisory_and_hash_bound(self):
        rid = self.complete_safe_meeting()
        r = self.run_cli("handoff", rid, "--stdout")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["schema"], "kaigi.handoff.v1")
        self.assertFalse(data["authority"]["execution_authorized"])
        self.assertTrue(data["authority"]["requires_separate_authority"])
        self.assertEqual(len(data["source"]["packet_sha256"]), 64)
        self.assertEqual(len(data["handoff_sha256"]), 64)

    def test_audit_reports_packet_pass(self):
        rid = self.complete_safe_meeting()
        r = self.run_cli("audit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(rid, r.stdout)
        self.assertIn("proof=PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()
