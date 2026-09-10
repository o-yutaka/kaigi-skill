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


class Handler(BaseHTTPRequestHandler):
    messages = []
    sessions = []
    status = {
        "claude": {"available": True, "role": ""},
        "codex": {"available": True, "role": ""},
        "chatgpt": {"available": True, "role": ""},
    }
    templates = [
        {"id": "planning", "name": "Planning", "description": "plan", "roles": ["planner", "challenger", "synthesiser"]},
        {"id": "debate", "name": "Debate", "description": "debate", "roles": ["proposer", "for", "against", "moderator"]},
    ]
    lock = threading.Lock()

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
    def add_agent(cls, sender, body, channel="general"):
        with cls.lock:
            cls.messages.append({
                "id": len(cls.messages) + 1,
                "sender": sender,
                "text": body,
                "channel": channel,
                "timestamp": time.time(),
            })

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
        if parsed.path == "/api/messages":
            query = parse_qs(parsed.query)
            with self.lock:
                out = list(self.messages)
            if "since_id" in query:
                sid = int(query["since_id"][0])
                out = [m for m in out if m["id"] > sid]
            if "channel" in query:
                out = [m for m in out if m.get("channel", "general") == query["channel"][0]]
            if "limit" in query:
                out = out[-int(query["limit"][0]):]
            self.sendj(out)
            return
        if parsed.path == "/api/status":
            self.sendj(self.status)
            return
        if parsed.path == "/api/sessions/templates":
            self.sendj(self.templates)
            return
        if parsed.path == "/api/sessions/active":
            self.sendj(None)
            return
        self.sendj({"error": "notfound"}, 404)

    def do_POST(self):
        if not self.authed():
            self.sendj({"error": "auth"}, 403)
            return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/send":
            with self.lock:
                msg = {
                    "id": len(self.messages) + 1,
                    "sender": "user",
                    "text": payload["text"],
                    "channel": payload["channel"],
                    "timestamp": time.time(),
                }
                self.messages.append(msg)
            self.sendj(msg)
            text = payload["text"]
            channel = payload["channel"]
            if "KAIGI COUNCIL / ROUND 1" in text:
                threading.Timer(0.03, lambda: [self.add_agent(a, f"{a} independent", channel) for a in ("claude", "codex", "chatgpt")]).start()
            elif "KAIGI COUNCIL / ROUND 2" in text:
                threading.Timer(0.03, lambda: [self.add_agent(a, f"{a} dissent", channel) for a in ("claude", "codex", "chatgpt")]).start()
            elif "KAIGI COUNCIL / FINAL" in text:
                target = text.split()[0].lstrip("@")
                threading.Timer(0.03, lambda: self.add_agent(target, "DECISION: ship", channel)).start()
            return
        if self.path == "/api/sessions/start":
            template = next(x for x in self.templates if x["id"] == payload["template_id"])
            cast = payload.get("cast") or {
                role: list(self.status)[i % len(self.status)]
                for i, role in enumerate(template["roles"])
            }
            session = {
                "id": len(self.sessions) + 1,
                "cast": cast,
                "template_id": template["id"],
                "goal": payload["goal"],
            }
            self.sessions.append(session)
            self.sendj(session)
            return
        self.sendj({"error": "notfound"}, 404)

    def log_message(self, *_):
        pass


class CliTest(unittest.TestCase):
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
        with Handler.lock:
            Handler.messages = [{
                "id": 1,
                "sender": "claude",
                "text": "ready",
                "channel": "general",
                "timestamp": time.time(),
            }]
            Handler.sessions = []
        if self.state.exists():
            import shutil
            shutil.rmtree(self.state)
        cfg = self.home / "config.local.toml"
        if cfg.exists():
            cfg.unlink()

    def run_cli(self, *args, input_text=None, timeout=8):
        return subprocess.run(
            [str(CLI), *args], env=self.env, text=True, input=input_text,
            capture_output=True, timeout=timeout,
        )

    def test_status_log_agents(self):
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude", r.stdout)
        r = self.run_cli("log", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude: ready", r.stdout)
        r = self.run_cli("agents")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("chatgpt", r.stdout)

    def test_say_shortcut(self):
        self.assertEqual(self.run_cli("say", "hello", "world").returncode, 0)
        self.assertEqual(self.run_cli("@claude", "check", "this").returncode, 0)
        self.assertEqual(Handler.messages[-1]["text"], "@claude check this")

    def test_convene_native_and_cast_is_persisted(self):
        r = self.run_cli(
            "convene", "ship", "the", "feature", "--template", "planning",
            "--agents", "claude,codex,chatgpt", "--no-follow",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("会議開始", r.stdout)
        session = Handler.sessions[-1]
        self.assertEqual(session["cast"]["planner"], "claude")
        self.assertEqual(session["cast"]["challenger"], "codex")
        self.assertEqual(session["cast"]["synthesiser"], "chatgpt")
        hist = self.run_cli("history", "5")
        self.assertIn("detached", hist.stdout)

    def test_convene_council_full_result_history_export(self):
        r = self.run_cli(
            "convene", "parallel", "decision", "--agents", "claude,codex,chatgpt",
            "--round-timeout", "1",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("council完了", r.stdout)
        self.assertIn("run=", r.stdout)
        texts = [m["text"] for m in Handler.messages if m["sender"] == "user"]
        self.assertTrue(any("ROUND 1" in x for x in texts))
        self.assertTrue(any("ROUND 2" in x for x in texts))
        self.assertTrue(any("FINAL" in x for x in texts))
        result = self.run_cli("result")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DECISION: ship", result.stdout)
        as_json = self.run_cli("result", "--json")
        parsed = json.loads(as_json.stdout)
        self.assertEqual(parsed["state"], "complete")
        self.assertEqual(parsed["final_text"], "DECISION: ship")
        history = self.run_cli("history")
        self.assertIn("complete", history.stdout)
        output = pathlib.Path(self.tmp.name) / "result.md"
        exported = self.run_cli("export", "--output", str(output))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn("DECISION: ship", output.read_text())

    def test_api_add_is_idempotent_and_generic(self):
        r = self.run_cli(
            "api", "add", "localglm", "--base-url", "http://127.0.0.1:8080/v1",
            "--model", "glm-a",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli(
            "api", "add", "localglm", "--base-url", "http://127.0.0.1:8081/v1",
            "--model", "glm-b",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.home / "config.local.toml").read_text()
        self.assertEqual(text.count("[agents.localglm]"), 1)
        self.assertIn("glm-b", text)
        listed = self.run_cli("api", "list")
        self.assertIn("localglm", listed.stdout)
        self.assertIn("local", listed.stdout)

    def test_chatgpt_setup_is_idempotent(self):
        r = self.run_cli("chatgpt", "setup", "--model", "gpt-5.6")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("chatgpt", "setup", "--model", "gpt-5.6-luna")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.home / "config.local.toml").read_text()
        self.assertEqual(text.count("[agents.chatgpt]"), 1)
        self.assertIn("gpt-5.6-luna", text)
        self.assertIn("OPENAI_API_KEY", text)

    def test_room_exit(self):
        r = self.run_cli("room", "--tail", "2", input_text="/quit\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("you ›", r.stdout)


if __name__ == "__main__":
    unittest.main()
