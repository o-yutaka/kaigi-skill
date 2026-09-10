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


class CapabilityHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages = []
    status = {
        "alpha": {"available": True, "role": ""},
        "beta": {"available": True, "role": ""},
        "gamma": {"available": True, "role": ""},
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
        text = payload["text"]
        channel = payload.get("channel", "general")
        msg = self.add("user", text, channel)
        self.sendj(msg)
        targets = self.targets(text)
        if "KAIGI COUNCIL / ROUND 1" in text:
            threading.Timer(0.02, lambda: [self.add(a, f"{a} independent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / ROUND 2" in text:
            threading.Timer(0.02, lambda: [self.add(a, f"{a} dissent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / FINAL" in text and targets:
            threading.Timer(0.02, lambda: self.add(targets[0], "DECISION: capability cast verified", channel)).start()

    def log_message(self, *_):
        pass


class CapabilityCastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.home = root / "agentchattr"
        cls.home.mkdir()
        cls.state = root / "state"
        cls.config = root / "kaigi-config"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), CapabilityHandler)
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

[agents.gamma]
command = "true"
label = "Gamma"

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
            "KAIGI_CONFIG_DIR": str(cls.config),
        })
        cls.env.pop("KAIGI_SYNTH_AGENT", None)
        cls.env.pop("KAIGI_SYNTH_POLICY", None)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        time.sleep(0.06)
        with CapabilityHandler.lock:
            CapabilityHandler.messages = []
        import shutil
        if self.state.exists():
            shutil.rmtree(self.state)
        if self.config.exists():
            shutil.rmtree(self.config)
        self.state.mkdir(parents=True)
        self._set("alpha", "coding", "deep-reasoning", cost="local", speed="fast")
        self._set("beta", "red-team", "research", cost="free", speed="normal")
        self._set("gamma", "coding", cost="metered", speed="fast")
        self._set("cloud-x", "research", "vision", cost="metered", speed="fast")

    def run_cli(self, *args, timeout=10):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, capture_output=True, timeout=timeout)

    def _set(self, name, *caps, cost=None, speed=None):
        args = ["caps", "set", name, *caps]
        if cost:
            args += ["--cost", cost]
        if speed:
            args += ["--speed", speed]
        r = self.run_cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)

    def latest_run(self):
        rid = json.loads((self.state / "latest.json").read_text())["run_id"]
        return json.loads((self.state / "runs" / f"{rid}.json").read_text())

    def test_version_is_v7(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("7.0.0", r.stdout)

    def test_registry_round_trip(self):
        r = self.run_cli("caps", "show", "alpha", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("coding", data["capabilities"])
        self.assertIn("deep-reasoning", data["capabilities"])
        self.assertEqual(data["cost"], "local")
        self.assertEqual(data["speed"], "fast")

    def test_cast_covers_required_capabilities_without_vendor_names(self):
        r = self.run_cli("cast", "PRのセキュリティレビュー", "--need", "coding,red-team", "--max-agents", "2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(set(data["selected"]), {"alpha", "beta"})
        self.assertEqual(set(data["required"]), {"coding", "red-team"})
        self.assertEqual(set(data["coverage"]) & {"coding", "red-team"}, {"coding", "red-team"})
        self.assertNotIn("cloud-x", data["selected"])

    def test_cost_preference_beats_metered_for_same_capability(self):
        r = self.run_cli("cast", "code review", "--need", "coding", "--max-agents", "1", "--min-agents", "1", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["selected"], ["alpha"])

    def test_hard_capability_is_fail_closed(self):
        before = len(CapabilityHandler.messages)
        r = self.run_cli("cast", "visual inspection", "--need", "vision", "--max-agents", "2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("明示capability", r.stderr)
        self.assertEqual(len(CapabilityHandler.messages), before)

    def test_best_effort_can_continue_with_missing_hard_capability(self):
        r = self.run_cli(
            "cast", "visual inspection", "--need", "vision", "--max-agents", "2",
            "--best-effort-capabilities", "--json",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["missing_required"], ["vision"])

    def test_topic_inference_is_soft_and_multi_capability(self):
        r = self.run_cli("caps", "infer", "PRのセキュリティリスクを調査して実装修正", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("coding", data["inferred"])
        self.assertIn("research", data["inferred"])
        self.assertIn("red-team", data["inferred"])

    def test_bare_topic_capability_cast_reaches_proof_packet(self):
        r = self.run_cli(
            "PRの安全性を判断して実装修正案を出す",
            "--need", "coding,red-team", "--max-agents", "2", "--round-timeout", "1",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        run = self.latest_run()
        self.assertEqual(run["state"], "complete")
        self.assertEqual(set(run["participants"]), {"alpha", "beta"})
        plan = run.get("capability_plan")
        self.assertIsInstance(plan, dict)
        self.assertEqual(set(plan["required"]), {"coding", "red-team"})
        self.assertEqual(len(plan["registry_sha256"]), 64)
        packet_path = self.state / "packets" / f"{run['run_id']}.json"
        self.assertTrue(packet_path.is_file())
        packet = json.loads(packet_path.read_text())
        self.assertEqual(packet["capability_plan"]["registry_sha256"], plan["registry_sha256"])
        verify = self.run_cli("verify", run["run_id"])
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn("PASS", verify.stdout)


if __name__ == "__main__":
    unittest.main()
