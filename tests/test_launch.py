from __future__ import annotations

import os
import unittest

import kaigi_launch as launch


class _FakeProc:
    pid = 4242

    def poll(self):
        return None


class _Ops:
    @staticmethod
    def shell_line(agent):
        return f"python wrapper.py {agent}"


class WslLaunchTest(unittest.TestCase):
    def setUp(self):
        self.old_wsl = os.environ.get("WSL_DISTRO_NAME")
        os.environ["WSL_DISTRO_NAME"] = "Ubuntu"

    def tearDown(self):
        if self.old_wsl is None:
            os.environ.pop("WSL_DISTRO_NAME", None)
        else:
            os.environ["WSL_DISTRO_NAME"] = self.old_wsl

    def test_dry_run_uses_detached_wsl_path_not_windows_terminal(self):
        saved = (
            launch.core.ensure_server,
            launch.core.load_agents_config,
            launch.core.fetch_status,
            launch.core.online_agents,
        )
        launch.core.ensure_server = lambda: None
        launch.core.load_agents_config = lambda: {"codex": {"command": "python3"}}
        launch.core.fetch_status = lambda: {}
        launch.core.online_agents = lambda _status: []
        try:
            got = launch.launch_cli_agents_wsl(
                _Ops(),
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("original launcher used")),
                ["codex"],
                dry_run=True,
            )
            self.assertIn("dry-run:wsl-detached:", got["codex"])
            self.assertNotIn("windows-terminal", got["codex"])
        finally:
            (
                launch.core.ensure_server,
                launch.core.load_agents_config,
                launch.core.fetch_status,
                launch.core.online_agents,
            ) = saved

    def test_process_creation_is_not_success_until_presence_is_online(self):
        saved = (
            launch.core.ensure_server,
            launch.core.ensure_state_dirs,
            launch.core.load_agents_config,
            launch.core.fetch_status,
            launch.core.online_agents,
            launch._read_pid,
            launch._launch_one,
            launch.time.sleep,
        )
        calls = {"status": 0}

        def status():
            calls["status"] += 1
            if calls["status"] == 1:
                return {}
            return {"codex": {"available": True}}

        launch.core.ensure_server = lambda: None
        launch.core.ensure_state_dirs = lambda: None
        launch.core.load_agents_config = lambda: {"codex": {"command": "python3"}}
        launch.core.fetch_status = status
        launch.core.online_agents = lambda value: [k for k, v in value.items() if v.get("available")]
        launch._read_pid = lambda _agent: None
        launch._launch_one = lambda _ops, _agent: _FakeProc()
        launch.time.sleep = lambda _seconds: None
        try:
            got = launch.launch_cli_agents_wsl(
                _Ops(),
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("original launcher used")),
                ["codex"],
                wait=0,
            )
            self.assertEqual(got["codex"], "online")
            self.assertGreaterEqual(calls["status"], 2)
        finally:
            (
                launch.core.ensure_server,
                launch.core.ensure_state_dirs,
                launch.core.load_agents_config,
                launch.core.fetch_status,
                launch.core.online_agents,
                launch._read_pid,
                launch._launch_one,
                launch.time.sleep,
            ) = saved

    def test_non_wsl_delegates_to_original_launcher(self):
        os.environ.pop("WSL_DISTRO_NAME", None)
        original_is_wsl = launch._is_wsl
        launch._is_wsl = lambda: False
        try:
            got = launch.launch_cli_agents_wsl(
                _Ops(),
                lambda names, **kwargs: {names[0]: "original"},
                ["codex"],
            )
            self.assertEqual(got, {"codex": "original"})
        finally:
            launch._is_wsl = original_is_wsl


if __name__ == "__main__":
    unittest.main()
