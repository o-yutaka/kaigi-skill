#!/usr/bin/env python3
"""v8.2 relay hardening: recovery, liveness, and delivery proof.

This layer intentionally does not broaden remote authority. It keeps the v8.1
outbound-only relay contract, while closing proof gaps observed in live E2E:

1. A Council stage timeout leaves a locally persisted ``waiting`` run that is
   recoverable, yet v8.1 immediately terminal-failed the remote request.
   v8.2 gives that same bound run one bounded recovery window before failing.
2. AgentChattr ``available`` means wrapper/presence heartbeat, not end-to-end
   ability to consume the trigger and return a chat reply.
3. Queue emptiness alone is not delivery proof: upstream clears the queue before
   calling the tmux injector.  Recent Kaigi delivery receipts therefore report
   whether paste+Enter was accepted, without exposing prompt/transcript text.
4. ``relay start`` must spawn this v8.2 module, not fall back to the v8.1 file.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 compatibility
    tomllib = None

import kaigi_delivery as delivery
import kaigi_relay as base
import kaigi_relay_v81 as v81

REVISION = "8.2.1-delivery-proof"
_V81_EXEC = v81._exec_kaigi
_V81_PROGRESS = v81._progress
_RECOVERABLE_STAGES = {"round1_timeout", "round2_timeout", "final_timeout", "follow_timeout"}


def _agentchattr_data_dir() -> pathlib.Path:
    """Resolve AgentChattr's configured data dir without reading message content."""
    raw: str | None = None
    if tomllib is not None:
        merged: dict[str, Any] = {}
        for path in (base.core.HOME / "config.toml", base.core.HOME / "config.local.toml"):
            if not path.is_file():
                continue
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            server = data.get("server")
            if isinstance(server, dict):
                merged.update(server)
        value = merged.get("data_dir")
        if isinstance(value, str) and value.strip():
            raw = value.strip()
    path = pathlib.Path(raw or "data").expanduser()
    return path if path.is_absolute() else (base.core.HOME / path)


def _tmux_session_alive(agent: str) -> bool | None:
    """Return tmux process-surface liveness on POSIX, or None when not applicable."""
    if os.name == "nt" or not shutil.which("tmux"):
        return None
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", f"agentchattr-{agent}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    return result.returncode == 0


def _recent_delivery(agent: str) -> dict[str, Any] | None:
    """Return only a recent metadata receipt so stale prompts are not misattributed."""
    return delivery.read_receipt(agent, max_age_seconds=300.0)


def participant_liveness(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return metadata-only liveness/delivery evidence for Council participants.

    Queue *contents* are never read. A zero-byte queue after a trigger means only
    "consumed-or-empty" because upstream clears before injection.  If present,
    ``inject_state`` comes from a Kaigi receipt that stores a prompt hash only.
    It proves at most tmux paste+Enter acceptance, not MCP use or a model reply.
    """
    participants = [str(x) for x in run.get("participants", []) if str(x)]
    try:
        status = base.core.fetch_status()
    except Exception:
        status = {}
    data_dir = _agentchattr_data_dir()
    out: dict[str, dict[str, Any]] = {}
    for name in participants:
        info = status.get(name, {}) if isinstance(status, dict) else {}
        online = bool(isinstance(info, dict) and info.get("available"))
        queue = data_dir / f"{name}_queue.jsonl"
        try:
            if not queue.exists():
                queue_state = "missing"
            elif queue.stat().st_size > 0:
                queue_state = "unconsumed"
            else:
                queue_state = "consumed-or-empty"
        except OSError:
            queue_state = "unreadable"
        item: dict[str, Any] = {
            "presence_online": online,
            "queue_state": queue_state,
            "tmux_session": _tmux_session_alive(name),
        }
        receipt = _recent_delivery(name)
        if receipt:
            item["inject_state"] = str(receipt.get("state") or "unknown")
            if receipt.get("ui_state"):
                item["inject_ui_state"] = str(receipt.get("ui_state"))
            try:
                item["inject_age_s"] = round(max(0.0, time.time() - float(receipt["observed_unix"])), 1)
            except (KeyError, TypeError, ValueError):
                pass
        out[name] = item
    return out


def _compact_liveness(run: dict[str, Any] | None) -> str:
    if not run:
        return "participant-liveness: unavailable"
    snapshot = participant_liveness(run)
    if not snapshot:
        return "participant-liveness: no-participants"
    parts: list[str] = []
    for name, info in snapshot.items():
        tmux = info.get("tmux_session")
        tmux_text = "na" if tmux is None else ("up" if tmux else "down")
        inject = str(info.get("inject_state") or "none")
        ui = str(info.get("inject_ui_state") or "")
        inject_text = inject + (f"/{ui}" if ui else "")
        parts.append(
            f"{name}[presence={'up' if info.get('presence_online') else 'down'},"
            f"queue={info.get('queue_state')},tmux={tmux_text},inject={inject_text}]"
        )
    return "participant-liveness: " + "; ".join(parts)


def _recoverable(run: dict[str, Any] | None) -> bool:
    if not isinstance(run, dict):
        return False
    if str(run.get("state") or "") != "waiting":
        return False
    return str(run.get("stage") or "") in _RECOVERABLE_STAGES


def _exec_kaigi(req: dict[str, Any], options: dict[str, Any], max_execution: int) -> dict[str, Any]:
    """Run v8.1 execution, then recover the same persisted run once on stage timeout."""
    try:
        return _V81_EXEC(req, options, max_execution)
    except base.RelayError as first_exc:
        request_id = str(req.get("id") or "")
        bound = base.find_bound_run(request_id) if request_id else None
        if not _recoverable(bound):
            raise base.RelayError(f"{first_exc}\n{_compact_liveness(bound)}") from first_exc

        # A saved waiting run is proof that the kickoff/stage request was emitted.
        # Give only the same run a bounded grace window; do not start a second Council.
        retry = dict(options)
        requested = max(5.0, float(options.get("round_timeout", 60.0)))
        retry["round_timeout"] = max(15.0, min(60.0, requested))
        try:
            return _V81_EXEC(req, retry, max_execution)
        except base.RelayError as recover_exc:
            latest = base.find_bound_run(request_id) or bound
            raise base.RelayError(
                f"{recover_exc}\n"
                f"same-run recovery attempted after {bound.get('stage')} "
                f"(grace={retry['round_timeout']:.0f}s); no second Council was started.\n"
                f"{_compact_liveness(latest)}"
            ) from recover_exc


def _progress(req: dict[str, Any], *, pid: int | None = None) -> dict[str, Any]:
    """Extend v8.1 progress with a coarse recovery-ready flag only."""
    out = _V81_PROGRESS(req, pid=pid)
    request_id = str(req.get("id") or "")
    run = base.find_bound_run(request_id) if request_id else None
    if run and str(run.get("stage") or "") in _RECOVERABLE_STAGES:
        out["recovery_ready"] = True
    return out


def _is_relay_serve_cmd(cmd: str) -> bool:
    return "serve" in cmd and any(
        name in cmd for name in ("kaigi_relay.py", "kaigi_relay_v81.py", "kaigi_relay_v82.py")
    )


def _daemon_command(interval: Any) -> list[str]:
    """Build the detached command and bind it to this v8.2 module."""
    return [sys.executable, str(pathlib.Path(__file__).resolve()), "serve", "--interval", str(interval)]


def cmd_start(args: Any) -> int:
    """Start a detached v8.2 daemon; never silently downgrade to v8.1."""
    base.load_config()
    existing = v81._daemon_pid()
    if existing:
        print(f"✓ relay already running pid={existing}")
        return 0
    base.core.ensure_state_dirs()
    base.DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)
    command = _daemon_command(args.interval)
    with base.DAEMON_LOG.open("a", encoding="utf-8") as log:
        kwargs: dict[str, Any] = {
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs)
    base.PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.3)
    if not v81._daemon_pid():
        raise base.RelayError(f"relay daemon起動を確認できません。log={base.DAEMON_LOG}")
    print(f"✓ relay daemon started pid={proc.pid} revision={REVISION} log={base.DAEMON_LOG}")
    return 0


# Patch v8.1 in place so its existing serve loop, terminal failure contract,
# heartbeat and parser remain public while execution/start use v8.2 semantics.
v81.REVISION = REVISION
v81._progress = _progress
v81._is_relay_serve_cmd = _is_relay_serve_cmd
v81.cmd_start = cmd_start
base.REVISION = REVISION
base._exec_kaigi = _exec_kaigi
base.cmd_start = cmd_start


def main(argv: list[str] | None = None) -> int:
    return v81.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
