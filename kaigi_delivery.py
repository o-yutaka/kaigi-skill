#!/usr/bin/env python3
"""Runtime delivery proof for Kaigi-launched AgentChattr CLI wrappers.

AgentChattr's upstream queue watcher currently clears its queue before calling
its tmux injector, and the boolean injector result is not persisted.  A zero
byte queue therefore proves only that the watcher observed/cleared the trigger;
it does not prove that the provider TUI accepted the prompt.

This module adds a Kaigi-owned, privacy-safe observation layer without editing
AgentChattr on disk:

- wraps ``wrapper_unix.run_agent`` inside the Kaigi-launched wrapper process;
- records whether the tmux injector accepted paste+Enter;
- records only a SHA-256 of the injected prompt, never the prompt text;
- detects Codex's visible ``Hooks need review`` startup blocker;
- never trusts/approves hooks automatically;
- holds a blocked prompt in memory and submits it once if the normal Codex
  composer becomes ready within a bounded window.

The receipt is diagnostic evidence, not proof that the model used MCP or posted
a reply.  ``tmux-submit-ok`` means only that the upstream injector returned
success after paste+Enter.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable

import kaigi_core as core

DELIVERY_SCHEMA = "kaigi.delivery_receipt.v1"
DELIVERY_POLICY = "tmux-submit-receipt-v1"
DEFAULT_DEFER_SECONDS = float(os.environ.get("KAIGI_DELIVERY_DEFER_SECONDS", "120"))
DEFAULT_DEFER_POLL = float(os.environ.get("KAIGI_DELIVERY_DEFER_POLL", "0.5"))


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return cleaned or "agent"


def delivery_dir() -> pathlib.Path:
    override = os.environ.get("KAIGI_DELIVERY_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path(core.STATE_DIR) / "deliveries"


def receipt_path(agent: str) -> pathlib.Path:
    return delivery_dir() / f"{_safe_name(agent)}.json"


def _atomic_json(path: pathlib.Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _prompt_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_receipt(
    agent: str,
    state: str,
    *,
    session_name: str = "",
    provider: str = "",
    prompt_sha256: str = "",
    ui_state: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """Persist metadata-only delivery evidence; never persist injected text."""
    now = time.time()
    payload: dict[str, Any] = {
        "schema": DELIVERY_SCHEMA,
        "policy": DELIVERY_POLICY,
        "agent": str(agent),
        "state": str(state),
        "observed_at": dt.datetime.now().astimezone().isoformat(),
        "observed_unix": now,
    }
    if session_name:
        payload["session_name"] = str(session_name)
    if provider:
        payload["provider"] = str(provider)
    if prompt_sha256:
        payload["prompt_sha256"] = str(prompt_sha256)
    if ui_state:
        payload["ui_state"] = str(ui_state)
    if detail:
        # Callers pass controlled classifications (normally exception type), not arbitrary output.
        payload["detail"] = str(detail)[:120]
    _atomic_json(receipt_path(agent), payload)
    return payload


def read_receipt(agent: str, *, max_age_seconds: float | None = None) -> dict[str, Any] | None:
    path = receipt_path(agent)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != DELIVERY_SCHEMA:
        return None
    if max_age_seconds is not None:
        try:
            age = max(0.0, time.time() - float(data.get("observed_unix", 0)))
        except (TypeError, ValueError):
            return None
        if age > max_age_seconds:
            return None
    return data


def _command_basename(command: Any) -> str:
    if not isinstance(command, str):
        return ""
    value = command.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value


def _capture_visible_pane(session_name: str) -> str | None:
    """Read only the currently visible tmux pane, never scrollback/history."""
    if not session_name:
        return None
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", session_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def codex_ui_state(session_name: str) -> str:
    """Classify only known, visible Codex startup states.

    This intentionally does not parse conversation content.  It looks for
    stable UI chrome/blocker phrases in the visible pane only.
    """
    text = _capture_visible_pane(session_name)
    if text is None:
        return "pane-unavailable"
    lowered = text.lower()
    if (
        "hooks need review" in lowered
        or ("review hooks" in lowered and "continue without trusting" in lowered)
        or ("hook needs review before it can run" in lowered)
    ):
        return "blocked-hooks-review"
    if "ask codex to do anything" in lowered:
        return "ready"
    return "unknown"


def _attempt_inject(
    inject_fn: Callable[[str], Any],
    text: str,
    *,
    agent: str,
    session_name: str,
    provider: str,
    ui_state: str,
) -> bool:
    prompt_hash = _prompt_sha256(text)
    try:
        accepted = bool(inject_fn(text))
    except Exception as exc:
        write_receipt(
            agent,
            "tmux-submit-exception",
            session_name=session_name,
            provider=provider,
            prompt_sha256=prompt_hash,
            ui_state=ui_state,
            detail=type(exc).__name__,
        )
        return False
    write_receipt(
        agent,
        "tmux-submit-ok" if accepted else "tmux-submit-failed",
        session_name=session_name,
        provider=provider,
        prompt_sha256=prompt_hash,
        ui_state=ui_state,
    )
    return accepted


def observed_injector(
    inject_fn: Callable[[str], Any],
    *,
    agent: str,
    session_name: str,
    provider: str,
    defer_seconds: float = DEFAULT_DEFER_SECONDS,
    defer_poll: float = DEFAULT_DEFER_POLL,
) -> Callable[[str], bool]:
    """Wrap one upstream injector with receipts and a Codex hook-review gate."""
    pending: set[str] = set()
    pending_lock = threading.Lock()

    def observed(text: str) -> bool:
        prompt_hash = _prompt_sha256(text)
        ui_state = codex_ui_state(session_name) if provider == "codex" else "not-probed"

        if provider == "codex" and ui_state == "blocked-hooks-review":
            write_receipt(
                agent,
                "deferred-hooks-review",
                session_name=session_name,
                provider=provider,
                prompt_sha256=prompt_hash,
                ui_state=ui_state,
            )
            with pending_lock:
                if prompt_hash in pending:
                    return True
                pending.add(prompt_hash)

            def deferred() -> None:
                deadline = time.monotonic() + max(0.0, float(defer_seconds))
                try:
                    while time.monotonic() < deadline:
                        state = codex_ui_state(session_name)
                        if state == "ready":
                            _attempt_inject(
                                inject_fn,
                                text,
                                agent=agent,
                                session_name=session_name,
                                provider=provider,
                                ui_state=state,
                            )
                            return
                        time.sleep(max(0.05, float(defer_poll)))
                    write_receipt(
                        agent,
                        "blocked-hooks-timeout",
                        session_name=session_name,
                        provider=provider,
                        prompt_sha256=prompt_hash,
                        ui_state=codex_ui_state(session_name),
                    )
                finally:
                    with pending_lock:
                        pending.discard(prompt_hash)

            threading.Thread(
                target=deferred,
                name=f"kaigi-delivery-{_safe_name(agent)}",
                daemon=True,
            ).start()
            # The prompt is now owned by the deferred in-memory delivery attempt.
            # No hook trust/approval action is performed automatically.
            return True

        return _attempt_inject(
            inject_fn,
            text,
            agent=agent,
            session_name=session_name,
            provider=provider,
            ui_state=ui_state,
        )

    return observed


def patch_wrapper_unix(module: Any, selected_agent: str) -> bool:
    """Patch an imported AgentChattr wrapper_unix module for this process only."""
    if getattr(module, "_kaigi_delivery_patch", False):
        return True
    original = getattr(module, "run_agent", None)
    if not callable(original):
        return False
    signature = inspect.signature(original)

    def run_agent(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        session_name = str(
            bound.arguments.get("session_name") or f"agentchattr-{selected_agent}"
        )
        command = str(bound.arguments.get("command") or "")
        provider = _command_basename(command)
        start_watcher = bound.arguments.get("start_watcher")
        agent = str(bound.arguments.get("agent") or selected_agent)
        if callable(start_watcher):
            original_start_watcher = start_watcher

            def guarded_start_watcher(inject_fn: Callable[[str], Any]) -> Any:
                return original_start_watcher(
                    observed_injector(
                        inject_fn,
                        agent=agent,
                        session_name=session_name,
                        provider=provider,
                    )
                )

            bound.arguments["start_watcher"] = guarded_start_watcher
        return original(*bound.args, **bound.kwargs)

    module.run_agent = run_agent
    module._kaigi_delivery_patch = True
    module._kaigi_delivery_original_run_agent = original
    return True


def install_wrapper_patch(selected_agent: str) -> bool:
    """Install the POSIX/tmux runtime patch after AgentChattr HOME is importable."""
    if sys.platform == "win32":
        return False
    try:
        import wrapper_unix  # type: ignore
    except Exception:
        return False
    return patch_wrapper_unix(wrapper_unix, selected_agent)
