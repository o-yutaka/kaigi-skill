#!/usr/bin/env python3
"""v8 relay hardening: progress visibility, bounded stages, safe daemon teardown.

This module patches kaigi_relay without changing the stable v8 public command surface.
It is intentionally provider-neutral and never expands remote execution authority.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
from typing import Any

import kaigi_relay as base

REVISION = "8.1-progress-watchdog"
ACTIVE_FILE = base.core.STATE_DIR / "relay-active.json"


def _write_active(data: dict[str, Any] | None) -> None:
    if data is None:
        ACTIVE_FILE.unlink(missing_ok=True)
        return
    payload = dict(data)
    payload["revision"] = REVISION
    payload["observed_at"] = dt.datetime.now().astimezone().isoformat()
    base._atomic_json(ACTIVE_FILE, payload, mode=0o600)


def _progress(req: dict[str, Any], *, pid: int | None = None) -> dict[str, Any]:
    request_id = str(req.get("id") or "")
    run = base.find_bound_run(request_id) if request_id else None
    if not run:
        out: dict[str, Any] = {
            "request_id": request_id,
            "run_id": None,
            "stage": "preparing",
            "state": "running",
            "round1_replies": 0,
            "round2_replies": 0,
        }
    else:
        out = {
            "request_id": request_id,
            "run_id": str(run.get("run_id") or "") or None,
            "stage": str(run.get("stage") or "unknown"),
            "state": str(run.get("state") or "unknown"),
            "round1_replies": len(run.get("round1") or {}),
            "round2_replies": len(run.get("round2") or {}),
            "participants": len(run.get("participants") or []),
            "synth": str(run.get("synth") or "") or None,
        }
    if pid is not None:
        out["pid"] = pid
    return out


class ProgressHeartbeat(base.Heartbeat):
    """Lease heartbeat plus privacy-preserving stage telemetry."""

    def _loop(self) -> None:
        interval = max(5, min(15, self.lease // 6))
        while not self.stop_event.wait(interval):
            try:
                progress = _progress(self.req)
                self.client.action(
                    "heartbeat",
                    id=str(self.req["id"]),
                    claim_token=str(self.req["claim_token"]),
                    lease_seconds=self.lease,
                    run_id=progress.get("run_id"),
                    stage=progress.get("stage"),
                    progress={
                        k: v for k, v in progress.items()
                        if k in {"state", "round1_replies", "round2_replies", "participants", "synth"}
                    },
                )
                _write_active(progress)
            except Exception as exc:
                base.core.eprint(f"relay heartbeat warning: {exc}")


def _terminate_proc(proc: subprocess.Popen[Any]) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _exec_kaigi(req: dict[str, Any], options: dict[str, Any], max_execution: int) -> dict[str, Any]:
    """Execute one Council with stage-aware stall detection.

    A remote request may choose a round timeout, but it cannot turn the worker into an
    unbounded process runner. Preparation and each Council stage get independent
    watchdogs; the local max_execution setting remains an upper bound.
    """
    request_id = str(req["id"])
    bound = base.find_bound_run(request_id)
    if bound:
        if bound.get("state") == "complete":
            return base._verified_packet(str(bound["run_id"]))
        argv = ["recover", str(bound["run_id"]), "--round-timeout", str(options.get("round_timeout", 60))]
    else:
        argv = base.build_decide_argv(str(req["topic"]), options)

    round_timeout = max(5.0, float(options.get("round_timeout", 60.0)))
    preparation_budget = max(60.0, min(180.0, round_timeout + 60.0))
    stage_budget = max(45.0, round_timeout + 35.0)
    derived_overall = max(180.0, preparation_budget + (3.0 * stage_budget) + 30.0)
    effective_max = min(float(max_execution), derived_overall)

    before = base._run_ids()
    base.EXEC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = base.EXEC_LOG_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", request_id) + ".log")
    env = os.environ.copy()
    env["KAIGI_RELAY_REQUEST_ID"] = request_id
    cli = pathlib.Path(__file__).resolve().with_name("kaigi")
    started = time.monotonic()
    last_stage = "preparing"
    stage_started = started
    discovered: dict[str, Any] | None = bound

    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            f"\n[{dt.datetime.now().astimezone().isoformat()}] revision={REVISION} "
            f"argv={json.dumps(argv, ensure_ascii=False)} effective_max={effective_max:.1f}s\n"
        )
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, str(cli), *argv],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            _write_active(_progress(req, pid=proc.pid))
            while proc.poll() is None:
                if not discovered:
                    discovered = base._bind_new_run(req, before)
                current = _progress(req, pid=proc.pid)
                stage = str(current.get("stage") or "preparing")
                if stage != last_stage:
                    last_stage = stage
                    stage_started = time.monotonic()
                _write_active(current)

                elapsed = time.monotonic() - started
                stage_elapsed = time.monotonic() - stage_started
                if not discovered and elapsed > preparation_budget:
                    _terminate_proc(proc)
                    raise base.RelayError(
                        f"local meeting preparation stall stage=preparing elapsed={elapsed:.1f}s"
                    )
                if discovered and stage not in {"complete", "failed"} and stage_elapsed > stage_budget:
                    _terminate_proc(proc)
                    raise base.RelayError(
                        f"local meeting stage stall stage={stage} elapsed={stage_elapsed:.1f}s "
                        f"run={current.get('run_id')}"
                    )
                if elapsed > effective_max:
                    _terminate_proc(proc)
                    raise base.RelayError(
                        f"local meeting execution timeout ({effective_max:.0f}s) stage={stage} "
                        f"run={current.get('run_id')}"
                    )
                time.sleep(0.2)
            code = int(proc.returncode or 0)
        finally:
            _write_active(None)

    if not bound:
        bound = base.find_bound_run(request_id) or base._bind_new_run(req, before)
    if code != 0:
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
        except OSError:
            tail = ""
        run_hint = f" run={bound.get('run_id')}" if bound else ""
        raise base.RelayError(f"kaigi exited {code}.{run_hint}\n{tail}".strip())
    if not bound:
        raise base.RelayError("relay requestに対応するrunを特定できません。")
    return base._verified_packet(str(bound["run_id"]))


def _kill_daemon_tree(pid: int) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode not in (0, 128):
            raise base.RelayError(f"relay process tree停止失敗 pid={pid}")
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        os.kill(pid, signal.SIGTERM)


def cmd_stop(args: Any) -> int:
    pid = base._daemon_pid()
    if not pid:
        _write_active(None)
        print("relay daemon is not running")
        return 0
    cmd = base._pid_command(pid)
    if "kaigi_relay.py" not in cmd or "serve" not in cmd:
        raise base.RelayError("PIDの実体がrelayではないため停止しません。")
    _kill_daemon_tree(pid)
    deadline = time.time() + 7
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    base.PID_FILE.unlink(missing_ok=True)
    _write_active(None)
    print(f"✓ relay daemon tree stopped pid={pid}")
    return 0


def cmd_status(args: Any) -> int:
    code = int(base._original_status(args) or 0)
    print(f"worker-revision: {REVISION}")
    if ACTIVE_FILE.is_file():
        try:
            active = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
        except Exception:
            active = {}
        if isinstance(active, dict) and active:
            print(
                "active  : request={request} run={run} stage={stage} r1={r1} r2={r2}".format(
                    request=active.get("request_id") or "?",
                    run=active.get("run_id") or "pending",
                    stage=active.get("stage") or "?",
                    r1=active.get("round1_replies", 0),
                    r2=active.get("round2_replies", 0),
                )
            )
    return code


# Patch the stable v8 implementation. build_parser resolves these module globals at call time.
base._original_status = base.cmd_status
base.Heartbeat = ProgressHeartbeat
base._exec_kaigi = _exec_kaigi
base.cmd_stop = cmd_stop
base.cmd_status = cmd_status
base.REVISION = REVISION


def main(argv: list[str] | None = None) -> int:
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
