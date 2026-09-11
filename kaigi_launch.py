#!/usr/bin/env python3
"""WSL-safe CLI wrapper launcher for Kaigi.

AgentChattr's POSIX wrapper owns the provider tmux session itself. Opening a
Windows Terminal tab from WSL adds a second terminal lifecycle and previously
allowed ``kaigi launch`` to print ``launched:windows-terminal`` even when the
wrapper never registered online.

On WSL this adapter starts the Kaigi compatibility wrapper as a detached Linux
process, captures its stdout/stderr to a local log, persists its PID, and only
returns ``online`` after AgentChattr presence confirms the wrapper.  Merely
creating a process is never reported as success.

Non-WSL platforms keep the upstream Kaigi launcher unchanged.
"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time
from typing import Any

import kaigi_core as core

LAUNCH_POLICY = "wsl-detached-online-verified-v1"
LOG_DIR = core.STATE_DIR / "wrapper-logs"


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in pathlib.Path("/proc/sys/kernel/osrelease").read_text(
            encoding="utf-8", errors="ignore"
        ).lower()
    except OSError:
        return False


def _pid_path(agent: str) -> pathlib.Path:
    return core.WRAPPER_PID_DIR / f"{agent}.pid"


def _read_pid(agent: str) -> int | None:
    try:
        pid = int(_pid_path(agent).read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        try:
            _pid_path(agent).unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return pid


def _write_pid(agent: str, pid: int) -> None:
    core.WRAPPER_PID_DIR.mkdir(parents=True, exist_ok=True)
    path = _pid_path(agent)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(str(pid) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _launch_one(ops: Any, agent: str) -> subprocess.Popen[Any]:
    core.ensure_state_dirs()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{agent}.log"
    line = ops.shell_line(agent)
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["bash", "-lc", line],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    _write_pid(agent, proc.pid)
    return proc


def launch_cli_agents_wsl(
    ops: Any,
    original: Any,
    names: list[str],
    *,
    wait: float = 8.0,
    dry_run: bool = False,
    here: bool = False,
) -> dict[str, str]:
    """Launch CLI wrappers inside WSL and require server-side online presence."""
    if not _is_wsl() or here:
        return original(names, wait=wait, dry_run=dry_run, here=here)

    core.ensure_server()
    cfgs = core.load_agents_config()
    online = set(core.online_agents(core.fetch_status()))
    results: dict[str, str] = {}
    launched: dict[str, subprocess.Popen[Any]] = {}

    for name in names:
        cfg = cfgs.get(name)
        if not cfg:
            results[name] = "not-configured"
            continue
        if cfg.get("type") == "api":
            results[name] = "api-agent"
            continue
        if name in online:
            results[name] = "online"
            continue
        command = str(cfg.get("command") or "").strip()
        if not command:
            results[name] = "command-missing-config"
            continue
        import shutil
        if not shutil.which(command):
            results[name] = f"command-not-found:{command}"
            continue
        if dry_run:
            results[name] = f"dry-run:wsl-detached:{ops.shell_line(name)}"
            continue

        existing = _read_pid(name)
        if existing:
            results[name] = f"wrapper-running-offline:pid={existing}"
            continue
        try:
            proc = _launch_one(ops, name)
        except OSError as exc:
            results[name] = f"launch-failed:{type(exc).__name__}"
            continue
        launched[name] = proc
        results[name] = f"starting:pid={proc.pid}"

    if dry_run:
        return results

    # Manual live testing on WSL showed registration/tmux startup may need more
    # than the historical eight-second terminal-only window.
    deadline = time.time() + max(12.0, float(wait))
    pending = set(launched)
    while pending and time.time() < deadline:
        try:
            current = set(core.online_agents(core.fetch_status()))
        except Exception:
            current = set()
        for name in list(pending):
            proc = launched[name]
            if name in current:
                results[name] = "online"
                pending.remove(name)
                continue
            code = proc.poll()
            if code is not None:
                results[name] = f"wrapper-exited:{code} log={LOG_DIR / (name + '.log')}"
                pending.remove(name)
        if pending:
            time.sleep(0.35)

    for name in pending:
        proc = launched[name]
        results[name] = f"offline-after-launch:pid={proc.pid} log={LOG_DIR / (name + '.log')}"
    return results


def apply_ops(ops: Any) -> None:
    """Patch one imported kaigi_ops module once."""
    if getattr(ops, "_kaigi_wsl_launch_patch", False):
        return
    original = ops.launch_cli_agents

    def launch(
        names: list[str], *, wait: float = 8.0, dry_run: bool = False, here: bool = False
    ) -> dict[str, str]:
        return launch_cli_agents_wsl(
            ops, original, names, wait=wait, dry_run=dry_run, here=here
        )

    ops.launch_cli_agents = launch
    ops._kaigi_wsl_launch_patch = True
    ops._kaigi_wsl_launch_original = original
