#!/usr/bin/env python3
"""v8.2 relay hardening: same-run recovery + privacy-safe participant liveness diagnostics.

This layer intentionally does not broaden remote authority. It keeps the v8.1
outbound-only relay contract, but fixes two proof gaps observed in live E2E:

1. A Council stage timeout leaves a locally persisted ``waiting`` run that is
   recoverable, yet v8.1 immediately terminal-failed the remote request.
   v8.2 gives that same bound run one bounded recovery window before failing.
2. AgentChattr ``available`` means wrapper/presence heartbeat, not end-to-end
   ability to consume the trigger and return a chat reply. When a Council still
   times out, v8.2 reports only privacy-safe delivery/liveness metadata (never
   queue contents or transcript text) so the broken boundary is observable.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 compatibility
    tomllib = None

import kaigi_relay as base
import kaigi_relay_v81 as v81

REVISION = "8.2-recovery-liveness"
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


def participant_liveness(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return metadata-only liveness evidence for Council participants.

    Queue *contents* are never read. A zero-byte queue after a trigger means only
    "consumed-or-empty"; it does not claim that the TUI/MCP/reply path succeeded.
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
        out[name] = {
            "presence_online": online,
            "queue_state": queue_state,
            "tmux_session": _tmux_session_alive(name),
        }
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
        parts.append(
            f"{name}[presence={'up' if info.get('presence_online') else 'down'},"
            f"queue={info.get('queue_state')},tmux={tmux_text}]"
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
    """Extend v8.1 progress with a coarse liveness classification only."""
    out = _V81_PROGRESS(req, pid=pid)
    request_id = str(req.get("id") or "")
    run = base.find_bound_run(request_id) if request_id else None
    if run and str(run.get("stage") or "") in _RECOVERABLE_STAGES:
        out["recovery_ready"] = True
    return out


# Patch v8.1 in place so its existing daemon lifecycle, terminal failure contract,
# progress heartbeat and parser remain the public implementation.
v81.REVISION = REVISION
v81._progress = _progress
base.REVISION = REVISION
base._exec_kaigi = _exec_kaigi


def main(argv: list[str] | None = None) -> int:
    return v81.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
