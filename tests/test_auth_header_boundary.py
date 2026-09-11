from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import kaigi_auth

TOKEN = "c" * 64


class AuthHeaderBoundaryTest(unittest.TestCase):
    def _core(self):
        def original_headers():
            token, source = core.resolve_token()
            headers = {"Accept": "application/json"}
            if token:
                if source == "bearer-env":
                    headers["Authorization"] = f"Bearer {token}"
                else:
                    headers["X-Session-Token"] = token
            return headers

        core = SimpleNamespace(
            SERVER_URL="http://127.0.0.1:8300",
            resolve_token=lambda: (None, "none"),
        )
        core.auth_headers = original_headers
        return core

    def test_discovered_session_is_never_synthesized_as_agent_bearer(self):
        core = self._core()
        with mock.patch.dict(
            os.environ,
            {"KAIGI_BEARER_TOKEN": "", "AGENTCHATTR_AGENT_TOKEN": "", "KAIGI_TOKEN": "", "AGENTCHATTR_TOKEN": ""},
            clear=False,
        ), mock.patch("kaigi_auth.discover_local_session_token", return_value=TOKEN):
            kaigi_auth.apply_core(core)
            headers = core.auth_headers()
        self.assertEqual(headers["X-Session-Token"], TOKEN)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(core.LOCAL_AUTH_POLICY, "local-session-discovery-v3-identity-separated")

    def test_explicit_bearer_remains_registered_agent_bearer(self):
        core = self._core()
        with mock.patch.dict(
            os.environ,
            {"KAIGI_BEARER_TOKEN": "bearer-secret", "AGENTCHATTR_AGENT_TOKEN": "", "KAIGI_TOKEN": "", "AGENTCHATTR_TOKEN": ""},
            clear=False,
        ):
            kaigi_auth.apply_core(core)
            headers = core.auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer bearer-secret")
        self.assertNotIn("X-Session-Token", headers)

    def test_explicit_session_remains_session_only(self):
        core = self._core()
        with mock.patch.dict(
            os.environ,
            {"KAIGI_BEARER_TOKEN": "", "AGENTCHATTR_AGENT_TOKEN": "", "KAIGI_TOKEN": "session-secret", "AGENTCHATTR_TOKEN": ""},
            clear=False,
        ):
            kaigi_auth.apply_core(core)
            headers = core.auth_headers()
        self.assertEqual(headers["X-Session-Token"], "session-secret")
        self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main()
