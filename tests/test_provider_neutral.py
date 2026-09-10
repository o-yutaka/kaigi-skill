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


class NeutralHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages = []
    status = {
        "alpha": {"available": True, "role": ""},
        "beta": {"available": True, "role": ""},
        "cloud-x": {"available": True, "role": ""},
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
        return [token[1:] for token in prefix.split() if token.startswith("@")]

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
        channel = payload.get("channel", "general")
        text = payload["text"]
        msg = self.add("user", text, channel)
        self.sendj(msg)
        targets = self.targets(text)
        if "KAIGI COUNCIL / ROUND 1" in text:
            threading.Timer(0.03, lambda: [self.add(a, f"{a} independent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / ROUND 2" in text:
            threading.Timer(0.03, lambda: [self.add(a, f"{a} dissent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / FINAL" in text and targets:
            threading.Timer(0.03, lambda: self.add(targets[0], f"DECISION: {targets[0]} synthesis", channel)).start()

    def log_message(self, *_):
        pass


class ProviderNeutralEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.home = root / "agentchattr"
        cls.home.mkdir()
        cls.state = root / "state"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), NeutralHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        (cls.home / "config.toml").write_text(
            """
[agents.alpha]
command = "true"
label = "Alpha"

[agents.beta]
command = "true"
label = "Beta"

[agents.cloud-x]
type = "api"
base_url = "https://example.invalid/v1"
model = "cloud-model"
api_key_env = "CLOUD_X_KEY"
label = "Cloud X"
""".strip() + "\n",
            encoding="utf-8",
        )
        cls.env = os.environ.copy()
        cls.env.update({
            "AGENTCHATTR_SERVER": f"http://127.0.0.1:{cls.port}",
            "KAIGI_TOKEN": "testtoken",
            "NO_COLOR": "1",
            "AGENTCHATTR_HOME": str(cls.home),
            "KAIGI_STATE_DIR": str(cls.state),
        })
        cls.env.pop("KAIGI_SYNTH_AGENT", None)
        cls.env.pop("KAIGI_SYNTH_POLICY", None)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        time.sleep(0.08)
        with NeutralHandler.lock:
            NeutralHandler.messages = []
        if self.state.exists():
            import shutil
            shutil.rmtree(self.state)
        self.state.mkdir(parents=True)

    def run_cli(self, *args, timeout=10):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, capture_output=True, timeout=timeout)

    def latest_run(self):
        rid = json.loads((self.state / "latest.json").read_text())["run_id"]
        return json.loads((self.state / "runs" / f"{rid}.json").read_text())

    def run_meeting(self, topic):
        r = self.run_cli(topic, "--round-timeout", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.latest_run()

    def test_safe_auto_completes_without_any_named_vendor(self):
        run = self.run_meeting("provider-neutral-one")
        self.assertEqual(run["state"], "complete")
        self.assertEqual(run["participants"], ["alpha", "beta"])
        self.assertNotIn("cloud-x", run["participants"])
        self.assertIn(run["synth"], {"alpha", "beta"})
        packet = self.state / "packets" / f"{run['run_id']}.json"
        self.assertTrue(packet.is_file())

    def test_balanced_synth_rotates_without_brand_priority(self):
        first = self.run_meeting("provider-neutral-one")
        # Keep ledger history but clear only chat messages before meeting two.
        with NeutralHandler.lock:
            NeutralHandler.messages = []
        second = self.run_meeting("provider-neutral-two")
        self.assertEqual(first["synth"], "alpha")
        self.assertEqual(second["synth"], "beta")

    def test_policy_reports_no_required_provider(self):
        r = self.run_cli("policy", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["provider_neutral"])
        self.assertEqual(data["required_providers"], [])
        self.assertFalse(data["synth"]["brand_priority"])
        self.assertFalse(data["safe_auto"]["brand_priority"])
        self.assertFalse(data["safe_auto"]["cloud_implicit"])

    def test_dry_run_selection_is_runtime_based(self):
        r = self.run_cli("decide", "neutral-preview", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alpha", r.stdout)
        self.assertIn("beta", r.stdout)
        self.assertIn("excluded-cloud", r.stdout)


if __name__ == "__main__":
    unittest.main()
