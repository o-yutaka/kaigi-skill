from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RelayV82ContractTest(unittest.TestCase):
    def _run(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_waiting_timeout_retries_same_bound_run_once(self):
        result = self._run(
            r'''
            import kaigi_relay as base
            import kaigi_relay_v82 as v82

            calls = []
            packet = {"packet_sha256": "a" * 64}
            run = {"run_id": "run-1", "state": "waiting", "stage": "round1_timeout", "participants": []}

            def fake_exec(req, options, max_execution):
                calls.append(dict(options))
                if len(calls) == 1:
                    raise base.RelayError("ROUND1 timeout")
                return packet

            v82._V81_EXEC = fake_exec
            base.find_bound_run = lambda _request_id: run
            got = v82._exec_kaigi({"id": "req-1"}, {"round_timeout": 90}, 900)
            assert got is packet
            assert len(calls) == 2
            assert calls[1]["round_timeout"] == 60.0
            print("ok")
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_nonrecoverable_error_is_not_replayed(self):
        result = self._run(
            r'''
            import kaigi_relay as base
            import kaigi_relay_v82 as v82

            calls = []
            def fake_exec(req, options, max_execution):
                calls.append(1)
                raise base.RelayError("synthetic hard failure")

            v82._V81_EXEC = fake_exec
            base.find_bound_run = lambda _request_id: None
            try:
                v82._exec_kaigi({"id": "req-2"}, {}, 900)
            except base.RelayError as exc:
                assert "synthetic hard failure" in str(exc)
            else:
                raise AssertionError("expected RelayError")
            assert calls == [1]
            print("ok")
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_liveness_reads_queue_metadata_not_contents(self):
        result = self._run(
            r'''
            import pathlib
            import tempfile
            import kaigi_relay as base
            import kaigi_relay_v82 as v82

            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                (root / "config.toml").write_text('[server]\ndata_dir = "runtime-data"\n', encoding="utf-8")
                data = root / "runtime-data"
                data.mkdir()
                (data / "alpha_queue.jsonl").write_text("secret-payload", encoding="utf-8")
                (data / "beta_queue.jsonl").write_text("", encoding="utf-8")
                base.core.HOME = root
                base.core.fetch_status = lambda: {
                    "alpha": {"available": True},
                    "beta": {"available": True},
                    "gamma": {"available": False},
                }
                v82._tmux_session_alive = lambda name: name == "alpha"
                v82._recent_delivery = lambda _name: None
                snap = v82.participant_liveness({"participants": ["alpha", "beta", "gamma"]})
                assert snap["alpha"] == {"presence_online": True, "queue_state": "unconsumed", "tmux_session": True}
                assert snap["beta"] == {"presence_online": True, "queue_state": "consumed-or-empty", "tmux_session": False}
                assert snap["gamma"] == {"presence_online": False, "queue_state": "missing", "tmux_session": False}
                rendered = v82._compact_liveness({"participants": ["alpha", "beta", "gamma"]})
                assert "secret-payload" not in rendered
                assert "queue=unconsumed" in rendered
                assert "inject=none" in rendered
            print("ok")
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_recent_delivery_receipt_is_exposed_without_prompt_text(self):
        result = self._run(
            r'''
            import hashlib
            import os
            import pathlib
            import tempfile
            import kaigi_delivery as delivery
            import kaigi_relay as base
            import kaigi_relay_v82 as v82

            with tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                os.environ["KAIGI_DELIVERY_DIR"] = str(root / "receipts")
                (root / "config.toml").write_text('[server]\ndata_dir = "runtime-data"\n', encoding="utf-8")
                data = root / "runtime-data"
                data.mkdir()
                (data / "codex_queue.jsonl").write_text("", encoding="utf-8")
                secret = "never-print-this-prompt"
                delivery.write_receipt(
                    "codex",
                    "tmux-submit-ok",
                    session_name="agentchattr-codex",
                    provider="codex",
                    prompt_sha256=hashlib.sha256(secret.encode()).hexdigest(),
                    ui_state="ready",
                )
                base.core.HOME = root
                base.core.fetch_status = lambda: {"codex": {"available": True}}
                v82._tmux_session_alive = lambda _name: True
                snap = v82.participant_liveness({"participants": ["codex"]})
                assert snap["codex"]["inject_state"] == "tmux-submit-ok"
                assert snap["codex"]["inject_ui_state"] == "ready"
                rendered = v82._compact_liveness({"participants": ["codex"]})
                assert "inject=tmux-submit-ok/ready" in rendered
                assert secret not in rendered
            print("ok")
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_daemon_start_command_is_bound_to_v82(self):
        result = self._run(
            r'''
            import pathlib
            import kaigi_relay as base
            import kaigi_relay_v81 as v81
            import kaigi_relay_v82 as v82

            command = v82._daemon_command(2.5)
            assert pathlib.Path(command[1]).name == "kaigi_relay_v82.py"
            assert command[2:] == ["serve", "--interval", "2.5"]
            assert v82._is_relay_serve_cmd("python /tmp/kaigi_relay_v82.py serve --interval 2")
            assert v82._is_relay_serve_cmd("python /tmp/kaigi_relay_v81.py serve --interval 2")
            assert not v82._is_relay_serve_cmd("python /tmp/kaigi_relay_v82.py status")
            assert base.cmd_start is v82.cmd_start
            assert v81.cmd_start is v82.cmd_start
            assert v82.REVISION == "8.2.1-delivery-proof"
            print("ok")
            '''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
