from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile
import time
import unittest

import kaigi_delivery as delivery


class DeliveryReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = os.environ.get("KAIGI_DELIVERY_DIR")
        os.environ["KAIGI_DELIVERY_DIR"] = self.tmp.name

    def tearDown(self):
        if self.old_dir is None:
            os.environ.pop("KAIGI_DELIVERY_DIR", None)
        else:
            os.environ["KAIGI_DELIVERY_DIR"] = self.old_dir
        self.tmp.cleanup()

    def test_receipt_is_0600_and_never_needs_prompt_text(self):
        secret = "TOP SECRET COUNCIL PROMPT"
        digest = hashlib.sha256(secret.encode()).hexdigest()
        receipt = delivery.write_receipt(
            "codex",
            "tmux-submit-ok",
            session_name="agentchattr-codex",
            provider="codex",
            prompt_sha256=digest,
            ui_state="ready",
        )
        path = delivery.receipt_path("codex")
        raw = path.read_text(encoding="utf-8")
        self.assertEqual(receipt["prompt_sha256"], digest)
        self.assertNotIn(secret, raw)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(delivery.read_receipt("codex")["state"], "tmux-submit-ok")

    def test_ready_codex_records_tmux_submit_result(self):
        original_state = delivery.codex_ui_state
        delivery.codex_ui_state = lambda _session: "ready"
        seen = []
        try:
            inject = delivery.observed_injector(
                lambda text: seen.append(text) or True,
                agent="codex",
                session_name="agentchattr-codex",
                provider="codex",
            )
            self.assertTrue(inject("read the meeting"))
            self.assertEqual(seen, ["read the meeting"])
            receipt = delivery.read_receipt("codex")
            self.assertEqual(receipt["state"], "tmux-submit-ok")
            self.assertEqual(receipt["ui_state"], "ready")
            self.assertNotIn("read the meeting", delivery.receipt_path("codex").read_text())
        finally:
            delivery.codex_ui_state = original_state

    def test_hook_review_is_not_auto_approved_and_prompt_is_deferred_once(self):
        original_state = delivery.codex_ui_state
        states = iter(["blocked-hooks-review", "blocked-hooks-review", "ready"])
        last = ["blocked-hooks-review"]

        def state(_session):
            try:
                last[0] = next(states)
            except StopIteration:
                pass
            return last[0]

        delivery.codex_ui_state = state
        seen = []
        try:
            inject = delivery.observed_injector(
                lambda text: seen.append(text) or True,
                agent="codex",
                session_name="agentchattr-codex",
                provider="codex",
                defer_seconds=1.0,
                defer_poll=0.01,
            )
            # Accepted into the in-memory deferred path; no keystroke is sent to
            # the hook trust dialog itself.
            self.assertTrue(inject("deferred prompt"))
            self.assertEqual(seen, [])
            deadline = time.time() + 1.0
            receipt = None
            while time.time() < deadline:
                receipt = delivery.read_receipt("codex")
                if seen and receipt and receipt.get("state") == "tmux-submit-ok":
                    break
                time.sleep(0.01)
            self.assertEqual(seen, ["deferred prompt"])
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["state"], "tmux-submit-ok")
            self.assertEqual(receipt["ui_state"], "ready")
        finally:
            delivery.codex_ui_state = original_state

    def test_patch_wrapper_unix_routes_watcher_through_observer(self):
        original_state = delivery.codex_ui_state
        delivery.codex_ui_state = lambda _session: "ready"

        class FakeUnix:
            def __init__(self):
                self.injected = []

            def run_agent(self, command, start_watcher, agent, session_name=None):
                def raw_inject(text):
                    self.injected.append(text)
                    return True
                start_watcher(raw_inject)
                return 7

        fake = FakeUnix()
        triggered = []

        def start_watcher(inject_fn):
            triggered.append(inject_fn("watcher payload"))

        try:
            self.assertTrue(delivery.patch_wrapper_unix(fake, "codex"))
            code = fake.run_agent(
                command="/usr/local/bin/codex",
                start_watcher=start_watcher,
                agent="codex",
                session_name="agentchattr-codex",
            )
            self.assertEqual(code, 7)
            self.assertEqual(fake.injected, ["watcher payload"])
            self.assertEqual(triggered, [True])
            self.assertEqual(delivery.read_receipt("codex")["state"], "tmux-submit-ok")
        finally:
            delivery.codex_ui_state = original_state


if __name__ == "__main__":
    unittest.main()
