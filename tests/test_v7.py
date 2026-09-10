import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "kaigi"


class V7Handler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages = []
    jobs = []
    job_posts = 0
    permanent_deletes = 0
    fail_patch = False
    status = {
        "claude": {"available": True, "role": ""},
        "codex": {"available": True, "role": ""},
    }

    def authed(self):
        return self.headers.get("X-Session-Token") == "testtoken"

    def sendj(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

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
        return [x[1:] for x in prefix.split() if x.startswith("@")]

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
        q = parse_qs(p.query)
        if p.path == "/api/status":
            self.sendj(self.status)
            return
        if p.path == "/api/messages":
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
        if p.path == "/api/jobs":
            with self.lock:
                out = [dict(j) for j in self.jobs]
            if q.get("channel", [""])[0]:
                out = [j for j in out if j.get("channel") == q["channel"][0]]
            if q.get("status", [""])[0]:
                out = [j for j in out if j.get("status") == q["status"][0]]
            self.sendj(out)
            return
        self.sendj({"error": "notfound"}, 404)

    def do_POST(self):
        from urllib.parse import urlparse
        p = urlparse(self.path)
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        if p.path == "/api/send":
            msg = self.add("user", body["text"], body.get("channel", "general"))
            self.sendj(msg)
            targets = self.targets(body["text"])
            channel = body.get("channel", "general")
            if "KAIGI COUNCIL / ROUND 1" in body["text"]:
                threading.Timer(0.02, lambda: [self.add(a, f"{a} independent", channel) for a in targets]).start()
            elif "KAIGI COUNCIL / ROUND 2" in body["text"]:
                threading.Timer(0.02, lambda: [self.add(a, f"{a} dissent", channel) for a in targets]).start()
            elif "KAIGI COUNCIL / FINAL" in body["text"] and targets:
                final = "DECISION: ship\nWHY: evidence\nDISSENT: none\nRISKS: regression\nNEXT ACTIONS: queue"
                threading.Timer(0.02, lambda: self.add(targets[0], final, channel)).start()
            return
        if p.path == "/api/jobs":
            with self.lock:
                type(self).job_posts += 1
                job = {
                    "id": len(self.jobs) + 1,
                    "uid": f"job-{len(self.jobs)+1}",
                    "type": body.get("type", "job"),
                    "title": body.get("title", "")[:120],
                    "body": body.get("body", "")[:1000],
                    "status": "done",  # upstream JobStore.create default = ACTIVE
                    "channel": body.get("channel", "general"),
                    "created_by": body.get("created_by", "user"),
                    "assignee": body.get("assignee", ""),
                    "anchor_msg_id": body.get("anchor_msg_id"),
                    "messages": [],
                }
                self.jobs.append(job)
            self.sendj(dict(job))
            return
        self.sendj({"error": "notfound"}, 404)

    def do_PATCH(self):
        from urllib.parse import urlparse
        p = urlparse(self.path)
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        if p.path.startswith("/api/jobs/"):
            if type(self).fail_patch:
                self.sendj({"error": "forced patch failure"}, 500)
                return
            job_id = int(p.path.rsplit("/", 1)[1])
            with self.lock:
                job = next((j for j in self.jobs if j["id"] == job_id), None)
                if job is not None and "status" in body:
                    job["status"] = body["status"]
                result = dict(job) if job else None
            if result is None:
                self.sendj({"error": "notfound"}, 404)
            else:
                self.sendj(result)
            return
        self.sendj({"error": "notfound"}, 404)

    def do_DELETE(self):
        from urllib.parse import parse_qs, urlparse
        p = urlparse(self.path)
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        if p.path.startswith("/api/jobs/"):
            job_id = int(p.path.rsplit("/", 1)[1])
            permanent = parse_qs(p.query).get("permanent", [""])[0].lower() == "true"
            with self.lock:
                job = next((j for j in self.jobs if j["id"] == job_id), None)
                if permanent and job is not None:
                    self.jobs.remove(job)
                    type(self).permanent_deletes += 1
            self.sendj(job or {"ok": True})
            return
        self.sendj({"error": "notfound"}, 404)

    def log_message(self, *_):
        pass


class V7CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.home = root / "agentchattr"
        cls.home.mkdir()
        cls.state = root / "state"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), V7Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.env = os.environ.copy()
        cls.env.update({
            "AGENTCHATTR_SERVER": f"http://127.0.0.1:{cls.port}",
            "KAIGI_TOKEN": "testtoken",
            "NO_COLOR": "1",
            "AGENTCHATTR_HOME": str(cls.home),
            "KAIGI_STATE_DIR": str(cls.state),
        })
        (cls.home / "config.toml").write_text(
            '[agents.claude]\ncommand="true"\n\n[agents.codex]\ncommand="true"\n',
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        time.sleep(0.06)
        with V7Handler.lock:
            V7Handler.messages = []
            V7Handler.jobs = []
            V7Handler.job_posts = 0
            V7Handler.permanent_deletes = 0
            V7Handler.fail_patch = False
        if self.state.exists():
            import shutil
            shutil.rmtree(self.state)
        self.state.mkdir(parents=True)

    def run_cli(self, *args, timeout=12):
        return subprocess.run([str(CLI), *args], env=self.env, text=True, capture_output=True, timeout=timeout)

    def make_decision(self):
        r = self.run_cli("v7", "decision", "test", "--round-timeout", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        rid = json.loads((self.state / "latest.json").read_text())["run_id"]
        return rid

    def test_public_version_is_v7(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("7.0.0", r.stdout)

    def test_packet_has_runtime_provenance_and_structured_decision(self):
        rid = self.make_decision()
        packet = json.loads((self.state / "packets" / f"{rid}.json").read_text())
        self.assertEqual(packet["provenance"]["kaigi_version"], "7.0.0")
        self.assertEqual(len(packet["provenance"]["runtime_sha256"]), 64)
        self.assertEqual(packet["decision"]["sections"]["decision"], "ship")
        self.assertEqual(packet["decision"]["sections"]["why"], "evidence")
        self.assertEqual(packet["decision"]["sections"]["next_actions"], "queue")
        r = self.run_cli("verify", rid, "--live", "--current-runtime")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)

    def test_queue_creates_todo_and_is_idempotent(self):
        rid = self.make_decision()
        r = self.run_cli("queue", rid, "--assignee", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("status=open/TO DO", r.stdout)
        with V7Handler.lock:
            self.assertEqual(len(V7Handler.jobs), 1)
            job = dict(V7Handler.jobs[0])
            posts = V7Handler.job_posts
        self.assertEqual(job["status"], "open")
        self.assertEqual(job["assignee"], "claude")
        self.assertIn(f"KAIGI-RUN:{rid}", job["body"])
        self.assertIn("execution_authorized=false", job["body"])
        self.assertIsInstance(job["anchor_msg_id"], int)
        r2 = self.run_cli("queue", rid)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("already queued", r2.stdout)
        with V7Handler.lock:
            self.assertEqual(len(V7Handler.jobs), 1)
            self.assertEqual(V7Handler.job_posts, posts)

    def test_queue_rolls_back_if_todo_patch_fails(self):
        rid = self.make_decision()
        V7Handler.fail_patch = True
        r = self.run_cli("queue", rid)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rolled-back", r.stderr)
        with V7Handler.lock:
            self.assertEqual(V7Handler.jobs, [])
            self.assertEqual(V7Handler.permanent_deletes, 1)

    def test_queue_dry_run_does_not_write_jobs(self):
        rid = self.make_decision()
        r = self.run_cli("queue", rid, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no Jobs API write", r.stdout)
        with V7Handler.lock:
            self.assertEqual(V7Handler.jobs, [])
            self.assertEqual(V7Handler.job_posts, 0)

    def test_jobs_lists_upstream_todo(self):
        rid = self.make_decision()
        self.assertEqual(self.run_cli("queue", rid).returncode, 0)
        r = self.run_cli("jobs", "--status", "open")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("source=kaigi", r.stdout)
        self.assertIn("open", r.stdout)

    def test_invocation_binding_does_not_follow_foreign_latest(self):
        import kaigi_core as core
        import kaigi_v6 as v6
        import kaigi_v7 as v7

        observed = {}
        original_new = core.new_run
        original_load = core.load_run
        original_main = v6.main

        def fake_new(*_args, **_kwargs):
            return {"run_id": "mine"}

        def fake_load(run_id=None):
            return {"run_id": run_id or "foreign"}

        def fake_main(_argv):
            core.new_run("topic", "general", "council")
            observed["loaded"] = core.load_run()["run_id"]
            return 0

        try:
            core.new_run = fake_new
            core.load_run = fake_load
            v6.main = fake_main
            code = v7.run_v6(["decide", "x"], bind_invocation=True)
            self.assertEqual(code, 0)
            self.assertEqual(observed["loaded"], "mine")
        finally:
            core.new_run = original_new
            core.load_run = original_load
            v6.main = original_main


if __name__ == "__main__":
    unittest.main()
