import unittest
from types import SimpleNamespace

import kaigi_auth


class RelayAuthStatusTest(unittest.TestCase):
    def test_status_reports_local_auth_source_without_token(self):
        core = SimpleNamespace(resolve_token=lambda: ("secret-not-printed", "local-index-session"))
        text = kaigi_auth.auth_status_line(core)
        self.assertEqual(text, "auth    : ✓ local-index-session")
        self.assertNotIn("secret-not-printed", text)

    def test_missing_auth_is_explicit(self):
        core = SimpleNamespace(resolve_token=lambda: (None, "none"))
        self.assertEqual(kaigi_auth.auth_status_line(core), "auth    : MISSING none")


if __name__ == "__main__":
    unittest.main()
