from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import kaigi_auth


TOKEN = "c" * 64


class DualHeaderAuthTest(unittest.TestCase):
    def _core(self, server="http://127.0.0.1:8300"):
        return SimpleNamespace(
            SERVER_URL=server,
            resolve_token=lambda: (None, "none"),
            auth_headers=lambda: {"Accept": "application/json"},
        )

    def test_auto_discovered_loopback_session_sends_both_compatible_headers(self):
        core = self._core()
        with mock.patch.dict(
            os.environ,
            {"KAIGI_BEARER_TOKEN": "", "AGENTCHATTR_AGENT_TOKEN": "", "KAIGI_TOKEN": "", "AGENTCHATTR_TOKEN": ""},
            clear=False,
        ), mock.patch("kaigi_auth.discover_local_session_token", return_value=TOKEN):
            kaigi_auth.apply_core(core)
            headers = core.auth_headers()
        self.assertEqual(headers["X-Session-Token"], TOKEN)
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_explicit_bearer_keeps_bearer_only_semantics(self):
        core = self._core()
        with mock.patch.dict(os.environ, {"KAIGI_BEARER_TOKEN": "bearer-secret"}, clear=False):
            kaigi_auth.apply_core(core)
            headers = core.auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer bearer-secret")
        self.assertNotIn("X-Session-Token", headers)

    def test_explicit_session_keeps_session_header_semantics(self):
        core = self._core()
        with mock.patch.dict(
            os.environ,
            {"KAIGI_BEARER_TOKEN": "", "AGENTCHATTR_AGENT_TOKEN": "", "KAIGI_TOKEN": "session-secret"},
            clear=False,
        ):
            kaigi_auth.apply_core(core)
            headers = core.auth_headers()
        self.assertEqual(headers["X-Session-Token"], "session-secret")
        self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main()
