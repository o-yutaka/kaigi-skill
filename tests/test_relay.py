import json
import os
import pathlib
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "kaigi"


class AgentHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages = []

    @classmethod
    def reset(cls):
        with cls.lock:
            cls.messages = []

    @classmethod
    def add(cls, sender, text, channel="general"):
        with cls.lock:
            msg = {
                "id": len(cls.messages) + 1,
                "sender": sender,
                "text": text,
                "channel": channel,
                "timestamp": time.time(),
            }
            cls.messages.append(msg)
            return dict(msg)

    @staticmethod
    def targets(text):
        prefix = text.split("[KAIGI", 1)[0]
        return [token[1:] for token in prefix.split() if token.startswith("@")]

    def sendj(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return self.headers.get("X-Session-Token") == "testtoken"

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        p = urlparse(self.path)
        if p.path == "/":
            self.send_response(200)
            self.end_headers()
            return
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        if p.path == "/api/status":
            self.sendj({"alpha": {"available": True}, "beta": {"available": True}})
            return
        if p.path == "/api/messages":
            q = parse_qs(p.query)
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
            threading.Timer(0.03, lambda: [self.add(a, f"{a} independent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / ROUND 2" in text:
            threading.Timer(0.03, lambda: [self.add(a, f"{a} dissent", channel) for a in targets]).start()
        elif "KAIGI COUNCIL / FINAL" in text and targets:
            threading.Timer(0.03, lambda: self.add(targets[0], "DECISION: relay verified", channel)).start()

    def log_message(self, *_):
        pass


class RelayHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    requests_queue = []
    actions = []
    token = "worker-token"

    @classmethod
    def reset(cls):
        with cls.lock:
            cls.requests_queue = []
            cls.actions = []

    def sendj(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        action = body.get("action")
        if action == "pair":
            if body.get("code") != "PAIR-CODE":
                self.sendj({"error": "pair_code_invalid_or_expired"}, 401)
                return
            self.sendj({
                "ok": True,
                "worker_id": "22222222-2222-2222-2222-222222222222",
                "worker_name": body.get("worker_name", "test-worker"),
                "token": self.token,
                "protocol": 1,
            })
            return
        if self.headers.get("x-kaigi-worker-token") != self.token:
            self.sendj({"error": "unauthorized"}, 401)
            return
        with self.lock:
            self.actions.append(dict(body))
            if action == "claim":
                req = self.requests_queue.pop(0) if self.requests_queue else None
                self.sendj({"ok": True, "request": req})
                return
        if action == "ping":
            self.sendj({"ok": True, "service": "kaigi-relay", "protocol": 1, "auth": "worker", "worker_id": "worker"})
        elif action in {"start", "heartbeat", "complete", "fail"}:
            self.sendj({"ok": True})
        else:
            self.sendj({"error": "unknown_action"}, 400)

    def log_message(self, *_):
        pass


class RelayEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.agent_home = root / "agentchattr"
        cls.agent_home.mkdir()
        cls.state = root / "state"
        cls.config_dir = root / "config"
        cls.relay_config = cls.config_dir / "relay.json"
        cls.cap_registry = cls.config_dir / "capabilities.json"

        cls.agent_server = ThreadingHTTPServer(("127.0.0.1", 0), AgentHandler)
        cls.agent_port = cls.agent_server.server_address[1]
        cls.agent_thread = threading.Thread(target=cls.agent_server.serve_forever, daemon=True)
        cls.agent_thread.start()

        cls.relay_server = ThreadingHTTPServer(("127.0.0.1", 0), RelayHandler)
        cls.relay_port = cls.relay_server.server_address[1]
        cls.relay_thread = threading.Thread(target=cls.relay_server.serve_forever, daemon=True)
        cls.relay_thread.start()

        (cls.agent_home / "config.toml").write_text(
            """
[agents.alpha]
command = "true"
label = "Alpha"

[agents.beta]
command = "true"
label = "Beta"
""".strip() + "\n",
            encoding="utf-8",
        )
        cls.config_dir.mkdir(parents=True)
        cls.cap_registry.write_text(json.dumps({
            "schema": "kaigi.capability_registry.v1",
            "agents": {
                "alpha": {"capabilities": ["research"], "cost": "free", "speed": "fast"},
                "beta": {"capabilities": ["red-team"], "cost": "free", "speed": "normal"},
            },
        }), encoding="utf-8")

        cls.env = os.environ.copy()
        cls.env.update({
            "AGENTCHATTR_SERVER": f"http://127.0.0.1:{cls.agent_port}",
            "KAIGI_TOKEN": "testtoken",
            "AGENTCHATTR_HOME": str(cls.agent_home),
            "KAIGI_STATE_DIR": str(cls.state),
            "KAIGI_RELAY_CONFIG": str(cls.relay_config),
            "KAIGI_CAPABILITY_REGISTRY": str(cls.cap_registry),
            "NO_COLOR": "1",
        })

    @classmethod
    def tearDownClass(cls):
        cls.agent_server.shutdown()
        cls.agent_server.server_close()
        cls.relay_server.shutdown()
        cls.relay_server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        import shutil
        AgentHandler.reset()
        RelayHandler.reset()
        if self.state.exists():
            shutil.rmtree(self.state)
        self.state.mkdir(parents=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.relay_config.write_text(json.dumps({
            "url": f"http://127.0.0.1:{self.relay_port}",
            "worker_id": "22222222-2222-2222-2222-222222222222",
            "worker_name": "test-worker",
            "token": RelayHandler.token,
            "allow_remote_cloud": False,
            "lease_seconds": 300,
            "max_execution_seconds": 60,
        }), encoding="utf-8")
        self.relay_config.chmod(0o600)

    def run_cli(self, *args, timeout=15):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, capture_output=True, timeout=timeout)

    @staticmethod
    def request(request_id="11111111-1111-1111-1111-111111111111", claim="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", **options):
        return {
            "id": request_id,
            "claim_token": claim,
            "topic": "research security release decision",
            "options": options or {"need": ["research", "red-team"], "free_only": True, "round_timeout": 5},
            "requested_by": "chatgpt",
        }

    def test_pair_saves_worker_token_with_0600(self):
        self.relay_config.unlink(missing_ok=True)
        r = self.run_cli(
            "relay", "pair", "PAIR-CODE", "--url", f"http://127.0.0.1:{self.relay_port}", "--name", "paired-test"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.relay_config.read_text())
        self.assertEqual(data["token"], RelayHandler.token)
        self.assertEqual(data["worker_name"], "paired-test")
        self.assertEqual(stat.S_IMODE(self.relay_config.stat().st_mode), 0o600)
        self.assertIn("stored-local-only", r.stdout)

    def test_relay_once_runs_real_council_and_returns_verified_hashes(self):
        req = self.request()
        RelayHandler.requests_queue = [req]
        r = self.run_cli("relay", "once", timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        complete = next(x for x in RelayHandler.actions if x.get("action") == "complete")
        self.assertEqual(complete["result_text"], "DECISION: relay verified")
        self.assertRegex(complete["packet_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(complete["transcript_sha256"], r"^[0-9a-f]{64}$")
        run_id = complete["run_id"]
        run = json.loads((self.state / "runs" / f"{run_id}.json").read_text())
        self.assertEqual(run["relay_request_id"], req["id"])
        self.assertEqual(run["state"], "complete")
        self.assertTrue((self.state / "packets" / f"{run_id}.json").is_file())
        self.assertTrue((self.state / "relay-receipts" / f"{req['id']}.json").is_file())
        user_msgs = [m for m in AgentHandler.messages if m["sender"] == "user"]
        self.assertEqual(sum("ROUND 1" in m["text"] for m in user_msgs), 1)

    def test_receipt_replay_does_not_start_second_council(self):
        req = self.request()
        RelayHandler.requests_queue = [req]
        first = self.run_cli("relay", "once", timeout=20)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = len(AgentHandler.messages)
        RelayHandler.actions = []
        replay = dict(req)
        replay["claim_token"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        RelayHandler.requests_queue = [replay]
        second = self.run_cli("relay", "once", timeout=10)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(AgentHandler.messages), before)
        self.assertTrue(any(x.get("action") == "complete" for x in RelayHandler.actions))
        self.assertIn("replay-complete", second.stdout)

    def test_remote_cloud_is_denied_by_local_policy(self):
        req = self.request(allow_cloud=True)
        RelayHandler.requests_queue = [req]
        r = self.run_cli("relay", "once", timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(any("KAIGI COUNCIL" in m["text"] for m in AgentHandler.messages))
        fail = next(x for x in RelayHandler.actions if x.get("action") == "fail")
        self.assertIn("remote cloud", fail["error"])

    def test_version_is_v8(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("8.0.0", r.stdout)


if __name__ == "__main__":
    unittest.main()
