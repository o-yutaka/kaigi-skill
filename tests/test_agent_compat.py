from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import kaigi_agent_compat as compat


class ClawCodexCompatTest(unittest.TestCase):
    def test_detection_is_narrow_and_explicit_config_wins(self):
        self.assertTrue(compat.needs_clawcodex_compat("clawcodex", {"command": "clawcodex"}))
        self.assertTrue(compat.needs_clawcodex_compat("reviewer", {"command": "/usr/local/bin/clawcodex"}))
        self.assertTrue(compat.needs_clawcodex_compat("reviewer", {"command": "clawcodex.exe"}))
        self.assertFalse(compat.needs_clawcodex_compat("codex", {"command": "codex"}))
        self.assertFalse(
            compat.needs_clawcodex_compat(
                "clawcodex", {"command": "clawcodex", "mcp_inject": "settings_file"}
            )
        )

    def test_workspace_persists_proxy_url_but_never_bearer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspace = root / "workspace"
            original = root / "project"
            original.mkdir()
            secret = "super-secret-agent-bearer"
            mcp_path, settings_path = compat.prepare_clawcodex_workspace(
                workspace,
                proxy_url="http://127.0.0.1:45678/mcp",
                original_cwd=original,
            )
            mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                mcp,
                {"mcpServers": {"agentchattr": {"type": "http", "url": "http://127.0.0.1:45678/mcp"}}},
            )
            self.assertIn("agentchattr", settings["enabledMcpjsonServers"])
            self.assertEqual(settings["additionalWorkingDirectories"], [str(original)])
            self.assertNotIn("enableAllProjectMcpServers", settings)
            all_text = mcp_path.read_text(encoding="utf-8") + settings_path.read_text(encoding="utf-8")
            self.assertNotIn(secret, all_text)
            self.assertEqual(mcp_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(settings_path.stat().st_mode & 0o777, 0o600)

    def test_workspace_rejects_non_loopback_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "loopback"):
                compat.prepare_clawcodex_workspace(
                    pathlib.Path(tmp), proxy_url="https://example.com/mcp"
                )

    def test_runtime_patch_uses_proxy_and_does_not_inject_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "project"
            project.mkdir()
            old_workspace_root = compat.WORKSPACE_ROOT
            compat.WORKSPACE_ROOT = root / "state-workspaces"

            class FakeConfigLoader:
                def load_config(self, _root=None):
                    return {
                        "agents": {
                            "clawcodex": {"command": "clawcodex", "cwd": str(project)}
                        }
                    }

            class FakeWrapper:
                def _build_provider_launch(self, *args, **kwargs):
                    raise AssertionError("upstream builder should not be used for compat path")

            loader = FakeConfigLoader()
            wrapper = FakeWrapper()
            try:
                compat.install_runtime_patch(loader, wrapper, "clawcodex")
                config = loader.load_config(root)
                cfg = config["agents"]["clawcodex"]
                workspace = pathlib.Path(cfg["cwd"])
                self.assertTrue(cfg["_kaigi_clawcodex_compat"])
                self.assertEqual(pathlib.Path(cfg["_kaigi_original_cwd"]), project)

                secret = "registered-agent-secret"
                args, env, inject_env, settings_path = wrapper._build_provider_launch(
                    "clawcodex",
                    cfg,
                    "clawcodex",
                    root / "data",
                    "http://127.0.0.1:56789/mcp",
                    ["--some-user-arg"],
                    {"PATH": "/bin"},
                    token=secret,
                    mcp_cfg={"http_port": 8200},
                    project_dir=workspace,
                )
                self.assertEqual(args, ["--some-user-arg"])
                self.assertEqual(env, {"PATH": "/bin"})
                self.assertEqual(inject_env, {})
                self.assertEqual(settings_path, workspace / ".mcp.json")
                written = (workspace / ".mcp.json").read_text(encoding="utf-8")
                self.assertIn("127.0.0.1:56789", written)
                self.assertNotIn(secret, written)
            finally:
                compat.WORKSPACE_ROOT = old_workspace_root


if __name__ == "__main__":
    unittest.main()
