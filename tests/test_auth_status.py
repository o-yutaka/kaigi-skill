import contextlib
import io
import unittest

import kaigi_relay as base
import kaigi_relay_v81 as v81


class RelayAuthStatusTest(unittest.TestCase):
    def test_status_reports_local_auth_source(self):
        original_status = base._original_status
        original_resolve = base.core.resolve_token
        base._original_status = lambda _args: 0
        base.core.resolve_token = lambda: ("secret-not-printed", "local-index-session")
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = v81.cmd_status(None)
        finally:
            base._original_status = original_status
            base.core.resolve_token = original_resolve
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("auth    : local-index-session", text)
        self.assertNotIn("secret-not-printed", text)


if __name__ == "__main__":
    unittest.main()
