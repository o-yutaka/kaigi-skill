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


class OpsHandler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages = []

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
    def add(cls, sender, text):
        with cls.lock:
            cls.messages.append({
                "id": len(cls.messages) + 1,
                "sender": sender,
                "text": text,
                "channel": "general",
                "timestamp": time.time(),
            })

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
        if p.path == "/api/status":
            self.sendj({"claude": {"available": True}, "codex": {"available": True}})
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
        self.add("user", payload["text"])
        with self.lock:
            msg = dict(self.messages[-1])
        self.sendj(msg)
        text = payload["text"]
        if "KAIGI COUNCIL / ROUND 2" in text:
            threading.Timer(0.03, lambda: [self.add("claude", "claude dissent"), self.add("codex", "codex dissent")]).start()
        elif "KAIGI COUNCIL / FINAL" in text:
            threading.Timer(0.03, lambda: self.add("claude", "DECISION: resumed ship")).start()

    def log_message(self, *_):
        pass


class OpsCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.home = root / "agentchattr"
        cls.home.mkdir()
        cls.state = root / "state"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), OpsHandler)
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

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        time.sleep(0.08)
        with OpsHandler.lock:
            OpsHandler.messages = []
        if self.state.exists():
            import shutil
            shutil.rmtree(self.state)
        self.state.mkdir(parents=True)
        (self.state / "runs").mkdir()
        (self.home / "config.toml").write_text(
            '[agents.dummy]\ncommand = "python3"\nlabel = "Dummy"\n', encoding="utf-8"
        )
        (self.home / "wrapper.py").write_text("# fixture\n", encoding="utf-8")
        venv_bin = self.home / ".venv/bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        (venv_bin / "python").write_text("# fixture\n", encoding="utf-8")

    def run_cli(self, *args, timeout=8):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, capture_output=True, timeout=timeout)

    def write_run(self, run):
        path = self.state / "runs" / f"{run['run_id']}.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        (self.state / "latest.json").write_text(json.dumps({"run_id": run["run_id"]}), encoding="utf-8")

    def base_run(self, run_id="testrun"):
        return {
            "schema_version": 1,
            "run_id": run_id,
            "kind": "council",
            "state": "detached",
            "stage": "round1",
            "topic": "resume topic",
            "channel": "general",
            "server": self.env["AGENTCHATTR_SERVER"],
            "started_at": "2026-09-11T00:00:00+09:00",
            "updated_at": "2026-09-11T00:00:00+09:00",
            "participants": ["claude", "codex"],
            "roles": {"claude": "planner", "codex": "red-team"},
            "synth": "claude",
            "kickoff_message_id": 1,
        }

    def test_reconcile_late_final_without_resending(self):
        OpsHandler.add("user", "@claude @codex [KAIGI COUNCIL / ROUND 1] RUN=testrun")
        OpsHandler.add("claude", "claude independent")
        OpsHandler.add("codex", "codex independent")
        OpsHandler.add("user", "@claude @codex [KAIGI COUNCIL / ROUND 2] RUN=testrun")
        OpsHandler.add("claude", "claude dissent")
        OpsHandler.add("codex", "codex dissent")
        OpsHandler.add("user", "@claude [KAIGI COUNCIL / FINAL] RUN=testrun")
        OpsHandler.add("claude", "DECISION: late result")
        run = self.base_run()
        self.write_run(run)
        before = len(OpsHandler.messages)
        r = self.run_cli("reconcile")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("complete", r.stdout)
        self.assertIn("DECISION: late result", r.stdout)
        self.assertEqual(len(OpsHandler.messages), before)
        saved = json.loads((self.state / "runs/testrun.json").read_text())
        self.assertEqual(saved["state"], "complete")
        self.assertEqual(saved["final_text"], "DECISION: late result")

    def test_resume_from_saved_round1_finishes_without_restarting_round1(self):
        OpsHandler.add("user", "@claude @codex [KAIGI COUNCIL / ROUND 1] RUN=testrun")
        OpsHandler.add("claude", "claude independent")
        OpsHandler.add("codex", "codex independent")
        self.write_run(self.base_run())
        r = self.run_cli("resume", "--round-timeout", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("council完了", r.stdout)
        self.assertIn("DECISION: resumed ship", r.stdout)
        texts = [m["text"] for m in OpsHandler.messages if m["sender"] == "user"]
        self.assertEqual(sum("ROUND 1" in x for x in texts), 1)
        self.assertEqual(sum("ROUND 2" in x for x in texts), 1)
        self.assertEqual(sum("FINAL" in x for x in texts), 1)
        saved = json.loads((self.state / "runs/testrun.json").read_text())
        self.assertEqual(saved["state"], "complete")

    def test_launch_here_dry_run_never_starts_process(self):
        r = self.run_cli("launch", "dummy", "--here", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry-run:here:", r.stdout)
        self.assertIn("kaigi_agent_compat.py dummy", r.stdout)

    def test_public_version_is_v8(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("8.0.0", r.stdout)


if __name__ == "__main__":
    unittest.main()
