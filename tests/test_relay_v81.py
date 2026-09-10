import unittest

import kaigi_relay as base
import kaigi_relay_v81 as v81


class FakeClient:
    def __init__(self):
        self.actions = []

    def action(self, name, **payload):
        self.actions.append((name, payload))
        return {"ok": True}


class RelayV81FailureContractTest(unittest.TestCase):
    def test_claimed_processing_exception_reports_terminal_fail(self):
        client = FakeClient()
        req = {"id": "request-1", "claim_token": "claim-1"}
        original = base.process_request

        def boom(_client, _config, _req):
            raise base.RelayError("synthetic local failure")

        base.process_request = boom
        try:
            with self.assertRaisesRegex(base.RelayError, "synthetic local failure"):
                v81._process_claimed_with_terminal_fail(client, {}, req)
        finally:
            base.process_request = original

        self.assertEqual(len(client.actions), 1)
        name, payload = client.actions[0]
        self.assertEqual(name, "fail")
        self.assertEqual(payload["id"], "request-1")
        self.assertEqual(payload["claim_token"], "claim-1")
        self.assertIn("synthetic local failure", payload["error"])

    def test_success_does_not_emit_fail(self):
        client = FakeClient()
        req = {"id": "request-2", "claim_token": "claim-2"}
        original = base.process_request
        base.process_request = lambda _client, _config, _req: 0
        try:
            self.assertEqual(v81._process_claimed_with_terminal_fail(client, {}, req), 0)
        finally:
            base.process_request = original
        self.assertEqual(client.actions, [])

    def test_public_base_dispatch_is_patched_to_hardened_daemon(self):
        self.assertIs(base.cmd_serve, v81.cmd_serve)
        self.assertIs(base.cmd_start, v81.cmd_start)
        self.assertIs(base.cmd_stop, v81.cmd_stop)
        self.assertEqual(base.REVISION, "8.1-progress-watchdog")


if __name__ == "__main__":
    unittest.main()
