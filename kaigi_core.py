#!/usr/bin/env python3
"""kaigi v4 core — terminal-first multi-agent council client for agentchattr."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import pathlib
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

VERSION = "4.0.0"
SERVER_URL = os.environ.get("AGENTCHATTR_SERVER", "http://127.0.0.1:8300").rstrip("/")
HOME = pathlib.Path(os.environ.get("AGENTCHATTR_HOME", str(pathlib.Path.home() / "agentchattr"))).expanduser()
LOG_FILE = pathlib.Path(os.environ.get("AGENTCHATTR_LOG", "/tmp/agentchattr-server.log")).expanduser()
STATE_DIR = pathlib.Path(os.environ.get("KAIGI_STATE_DIR", str(pathlib.Path.home() / ".local/state/kaigi"))).expanduser()
RUNS_DIR = STATE_DIR / "runs"
LATEST_FILE = STATE_DIR / "latest.json"
PID_FILE = pathlib.Path(os.environ.get("KAIGI_SERVER_PID", str(STATE_DIR / "server.pid"))).expanduser()
WRAPPER_PID_DIR = STATE_DIR / "wrappers"
DEFAULT_CHANNEL = os.environ.get("KAIGI_CHANNEL", "general")
DEFAULT_WRAPPER = os.environ.get("KAIGI_WRAPPER", "lmstudio")
DEFAULT_INTERVAL = float(os.environ.get("KAIGI_POLL_INTERVAL", "1.0"))
DEFAULT_CHATGPT_MODEL = os.environ.get("KAIGI_CHATGPT_MODEL", "gpt-5.6")
COUNCIL_ROLES = ["planner", "red-team", "implementer", "evidence", "ux", "long-horizon"]
SYNTH_PRIORITY = ["chatgpt", "claude", "codex", "hermes"]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()

RESET = "" if NO_COLOR else "\033[0m"
DIM = "" if NO_COLOR else "\033[2m"
BOLD = "" if NO_COLOR else "\033[1m"
GREEN = "" if NO_COLOR else "\033[32m"
RED = "" if NO_COLOR else "\033[31m"
YELLOW = "" if NO_COLOR else "\033[33m"
CYAN = "" if NO_COLOR else "\033[36m"
SENDER_COLORS = [] if NO_COLOR else ["\033[36m", "\033[35m", "\033[32m", "\033[33m", "\033[34m", "\033[31m"]


class KaigiError(RuntimeError):
    pass


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def ensure_state_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    WRAPPER_PID_DIR.mkdir(parents=True, exist_ok=True)


def atomic_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def new_run(topic: str, channel: str, kind: str, **extra: Any) -> dict[str, Any]:
    ensure_state_dirs()
    stamp = dt.datetime.now().astimezone()
    run_id = stamp.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "kind": kind,
        "state": "running",
        "stage": "created",
        "topic": topic,
        "channel": channel,
        "server": SERVER_URL,
        "started_at": stamp.isoformat(),
        "updated_at": stamp.isoformat(),
        **extra,
    }
    save_run(run)
    return run


def save_run(run: dict[str, Any]) -> None:
    run["updated_at"] = dt.datetime.now().astimezone().isoformat()
    ensure_state_dirs()
    atomic_json(RUNS_DIR / f"{run['run_id']}.json", run)
    atomic_json(LATEST_FILE, {"run_id": run["run_id"]})


def run_fail(run: dict[str, Any], exc: Exception) -> None:
    run["state"] = "failed"
    run["error"] = str(exc)
    run["stage"] = run.get("stage") or "unknown"
    save_run(run)


def load_run(run_id: str | None = None) -> dict[str, Any]:
    if not run_id or run_id == "latest":
        try:
            run_id = json.loads(LATEST_FILE.read_text(encoding="utf-8"))["run_id"]
        except Exception as exc:
            raise KaigiError("保存済み会議がありません。") from exc
    path = RUNS_DIR / f"{run_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KaigiError(f"run が見つかりません: {run_id}") from exc
    if not isinstance(data, dict):
        raise KaigiError(f"run file が不正です: {path}")
    return data


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    ensure_state_dirs()
    runs: list[dict[str, Any]] = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                runs.append(data)
        except Exception:
            continue
        if len(runs) >= limit:
            break
    return runs


def sender_color(sender: str) -> str:
    if NO_COLOR:
        return ""
    n = sum((i + 1) * ord(ch) for i, ch in enumerate(sender))
    return SENDER_COLORS[n % len(SENDER_COLORS)]


def resolve_token() -> tuple[str | None, str]:
    bearer = os.environ.get("KAIGI_BEARER_TOKEN") or os.environ.get("AGENTCHATTR_AGENT_TOKEN")
    if bearer:
        return bearer.strip(), "bearer-env"
    session = os.environ.get("KAIGI_TOKEN") or os.environ.get("AGENTCHATTR_TOKEN")
    if session:
        return session.strip(), "session-env"
    if LOG_FILE.is_file():
        try:
            for line in reversed(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()):
                if "Session token:" in line:
                    token = line.split("Session token:", 1)[1].strip().split()[0]
                    if token:
                        return token, "server-log"
        except OSError:
            pass
    return None, "none"


def auth_headers() -> dict[str, str]:
    token, source = resolve_token()
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        if source == "bearer-env":
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["X-Session-Token"] = token
    return headers


def request_json(method: str, path: str, *, params: dict[str, Any] | None = None,
                 payload: dict[str, Any] | None = None, timeout: float = 5.0) -> Any:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = auth_headers()
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{SERVER_URL}{path}{query}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        hint = " 認証トークンを確認してください。" if exc.code in (401, 403) else ""
        raise KaigiError(f"HTTP {exc.code} {path}: {body or exc.reason}.{hint}") from exc
    except urllib.error.URLError as exc:
        raise KaigiError(f"agentchattr に接続できません: {exc.reason}") from exc


def server_alive(timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{SERVER_URL}/", method="GET"), timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def python_bin() -> pathlib.Path | None:
    for candidate in (HOME / ".venv/bin/python", HOME / "venv/bin/python"):
        if candidate.is_file():
            return candidate
    return None


def process_cmdline(pid: int) -> str:
    try:
        return pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def pid_alive(pid: int, fragment: str | None = None) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    if fragment and fragment not in process_cmdline(pid):
        return False
    return True


def read_pid(path: pathlib.Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def wrapper_pid_path(name: str) -> pathlib.Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
    return WRAPPER_PID_DIR / f"{safe}.pid"


def wrapper_running(name: str) -> bool:
    pid = read_pid(wrapper_pid_path(name))
    if pid and pid_alive(pid, f"wrapper_api.py {name}"):
        return True
    pgrep = shutil.which("pgrep")
    return bool(pgrep and subprocess.run(
        [pgrep, "-f", f"wrapper_api.py {name}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0)


def launch_wrapper(name: str, log_handle: Any | None = None) -> int | None:
    if not name or name.lower() in {"none", "off", "false", "0"}:
        return None
    wrapper, py = HOME / "wrapper_api.py", python_bin()
    if not wrapper.is_file() or not py or wrapper_running(name):
        return None
    ensure_state_dirs()
    owned_handle = None
    if log_handle is None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        owned_handle = LOG_FILE.open("a", encoding="utf-8")
        log_handle = owned_handle
    try:
        proc = subprocess.Popen(
            [str(py), str(wrapper), name], cwd=str(HOME),
            stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True
        )
        wrapper_pid_path(name).write_text(str(proc.pid), encoding="utf-8")
        return proc.pid
    finally:
        if owned_handle is not None:
            owned_handle.close()


def start_server(*, wrapper: str | None = None, quiet: bool = False) -> None:
    ensure_state_dirs()
    if server_alive():
        if not quiet:
            print(f"{GREEN}✓{RESET} agentchattr は既に起動中  {DIM}{SERVER_URL}{RESET}")
        if wrapper:
            pid = launch_wrapper(wrapper)
            if pid and not quiet:
                print(f"{GREEN}✓{RESET} wrapper {wrapper} を起動 (pid {pid})")
        return
    run_py, py = HOME / "run.py", python_bin()
    if not run_py.is_file():
        raise KaigiError(f"agentchattr が見つかりません: {run_py}\nAGENTCHATTR_HOME で場所を指定できます。")
    if not py:
        raise KaigiError(f"agentchattr の Python venv が見つかりません: {HOME}/.venv または {HOME}/venv")
    if not quiet:
        print(f"{CYAN}▶{RESET} agentchattr を起動しています…")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("TMUX", None)
    with LOG_FILE.open("w", encoding="utf-8") as h:
        proc = subprocess.Popen(
            [str(py), str(run_py)], cwd=str(HOME), stdout=h, stderr=subprocess.STDOUT,
            env=env, start_new_session=True
        )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + 20
    while time.time() < deadline and proc.poll() is None and not server_alive():
        time.sleep(0.4)
    if not server_alive():
        try:
            tail = "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-8:])
        except OSError:
            tail = ""
        raise KaigiError(f"agentchattr の起動に失敗しました。\n{tail}".rstrip())
    wrapper_name = DEFAULT_WRAPPER if wrapper is None else wrapper
    if wrapper_name:
        launch_wrapper(wrapper_name)
    if not quiet:
        print(f"{GREEN}✓{RESET} 起動完了  {DIM}{SERVER_URL}{RESET}")


def ensure_server() -> None:
    if not server_alive():
        start_server(quiet=True)


def stop_pidfile(path: pathlib.Path, expected_fragment: str) -> bool:
    pid = read_pid(path)
    if not pid:
        return False
    if not pid_alive(pid, expected_fragment):
        path.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    path.unlink(missing_ok=True)
    return True


def stop_server() -> bool:
    stopped = False
    ensure_state_dirs()
    for path in WRAPPER_PID_DIR.glob("*.pid"):
        pid = read_pid(path)
        if pid and pid_alive(pid, "wrapper_api.py"):
            try:
                os.kill(pid, signal.SIGTERM)
                stopped = True
            except ProcessLookupError:
                pass
        path.unlink(missing_ok=True)
    stopped = stop_pidfile(PID_FILE, "run.py") or stopped
    return stopped


def normalize_messages(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if isinstance(data, dict):
        for key in ("messages", "data", "items"):
            if isinstance(data.get(key), list):
                return [m for m in data[key] if isinstance(m, dict)]
    return []


def fetch_messages(*, limit: int | None = None, since_id: Any = None,
                   channel: str | None = None) -> list[dict[str, Any]]:
    params = {"limit": limit, "since_id": since_id, "channel": channel}
    return normalize_messages(request_json("GET", "/api/messages", params=params))


def send_message(text: str, channel: str = DEFAULT_CHANNEL) -> Any:
    return request_json("POST", "/api/send", payload={"text": text, "channel": channel})


def fetch_status() -> dict[str, Any]:
    data = request_json("GET", "/api/status")
    return data if isinstance(data, dict) else {}


def online_agents(status: dict[str, Any] | None = None) -> list[str]:
    status = fetch_status() if status is None else status
    return [str(name) for name, info in status.items() if isinstance(info, dict) and info.get("available")]


def fetch_templates() -> list[dict[str, Any]]:
    data = request_json("GET", "/api/sessions/templates")
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def msg_id(msg: dict[str, Any]) -> Any:
    return msg.get("id", msg.get("message_id", -1))


def latest_id(messages: list[dict[str, Any]], fallback: Any = -1) -> Any:
    return msg_id(messages[-1]) if messages else fallback


def format_time(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            if value > 10_000_000_000:
                value /= 1000
            return dt.datetime.fromtimestamp(value).strftime("%H:%M")
        except Exception:
            pass
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except Exception:
            pass
    return "--:--"


def render_message(msg: dict[str, Any], *, show_channel: bool = False) -> str:
    sender = str(msg.get("sender") or msg.get("author") or "unknown")
    text = str(msg.get("text") or msg.get("content") or "")
    stamp = format_time(msg.get("timestamp", msg.get("created_at")))
    channel = str(msg.get("channel") or DEFAULT_CHANNEL)
    prefix = f"{DIM}{stamp}{RESET} {sender_color(sender)}{BOLD}{sender}{RESET}"
    if show_channel and channel != DEFAULT_CHANNEL:
        prefix += f" {DIM}#{channel}{RESET}"
    lines = text.splitlines() or [""]
    return f"{prefix}: {lines[0]}" + ("" if len(lines) == 1 else "\n" + "\n".join("       " + x for x in lines[1:]))


def print_messages(messages: list[dict[str, Any]], *, channel: str | None = None) -> None:
    for msg in messages:
        if channel and str(msg.get("channel") or DEFAULT_CHANNEL) != channel:
            continue
        print(render_message(msg, show_channel=channel is None))


def _merge_agent_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for name, cfg in overlay.items():
        if isinstance(cfg, dict):
            merged = dict(result.get(name, {})) if isinstance(result.get(name), dict) else {}
            merged.update(cfg)
            result[name] = merged
    return result


def load_agents_config() -> dict[str, dict[str, Any]]:
    if tomllib is None:
        return {}
    agents: dict[str, Any] = {}
    for path in (HOME / "config.toml", HOME / "config.local.toml"):
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        section = data.get("agents", {})
        if isinstance(section, dict):
            agents = _merge_agent_config(agents, section)
    return {str(k): v for k, v in agents.items() if isinstance(v, dict)}


def is_local_api(cfg: dict[str, Any]) -> bool:
    if cfg.get("type") != "api":
        return False
    try:
        host = (urllib.parse.urlparse(str(cfg.get("base_url", ""))).hostname or "").lower()
    except Exception:
        return False
    return host in LOCAL_HOSTS


def api_agent_readiness(name: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    if cfg.get("type") != "api":
        return False, "not-api"
    env_name = str(cfg.get("api_key_env") or "")
    if env_name and not os.environ.get(env_name):
        return False, f"{env_name} missing"
    if not cfg.get("base_url"):
        return False, "base_url missing"
    return True, "ready"


def start_api_agents(names: list[str], *, wait: float = 5.0, include_cloud: bool = False,
                     quiet: bool = False) -> dict[str, str]:
    ensure_server()
    cfgs = load_agents_config()
    status_before = fetch_status()
    online_before = set(online_agents(status_before))
    selected: list[str] = []
    results: dict[str, str] = {}
    for name in names:
        cfg = cfgs.get(name)
        if not cfg:
            results[name] = "not-configured"
            continue
        if cfg.get("type") != "api":
            results[name] = "not-api"
            continue
        if name in online_before:
            results[name] = "online"
            continue
        if not include_cloud and not is_local_api(cfg):
            results[name] = "cloud-skipped"
            continue
        ready, why = api_agent_readiness(name, cfg)
        if not ready:
            results[name] = why
            continue
        selected.append(name)

    def launch(name: str) -> tuple[str, int | None]:
        return name, launch_wrapper(name)

    if selected:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(selected))) as ex:
            for name, pid in ex.map(launch, selected):
                results[name] = f"started:{pid}" if pid else ("running" if wrapper_running(name) else "start-failed")

    deadline = time.time() + max(0.0, wait)
    wanted = {n for n in selected if results.get(n, "").startswith(("started", "running"))}
    while wanted and time.time() < deadline:
        current = set(online_agents(fetch_status()))
        for n in list(wanted):
            if n in current:
                results[n] = "online"
                wanted.remove(n)
        if wanted:
            time.sleep(0.25)
    if not quiet:
        for name in names:
            print(f"{name:<16} {results.get(name, 'unknown')}")
    return results


def maybe_wake_for_council(args: argparse.Namespace) -> None:
    cfgs = load_agents_config()
    if not cfgs:
        return
    if args.agents:
        explicit = [x.strip().lstrip("@") for x in args.agents.split(",") if x.strip()]
        api_names = [n for n in explicit if cfgs.get(n, {}).get("type") == "api"]
        if api_names:
            start_api_agents(api_names, wait=args.wake_timeout, include_cloud=True, quiet=True)
        return
    if args.no_wake:
        return
    local_api = [n for n, cfg in cfgs.items() if is_local_api(cfg)]
    if local_api:
        start_api_agents(local_api, wait=args.wake_timeout, include_cloud=False, quiet=True)


def cmd_status(_args: argparse.Namespace) -> int:
    alive = server_alive()
    print(f"server   : {GREEN + 'UP' + RESET if alive else RED + 'DOWN' + RESET}  {SERVER_URL}")
    print(f"home     : {HOME} {'✓' if HOME.is_dir() else '✗'}")
    token, source = resolve_token()
    print(f"auth     : {'✓ ' + source if token else '✗ tokenなし'}")
    if not alive:
        return 1
    try:
        status = fetch_status()
        agents = online_agents(status)
        if agents:
            print("online   : " + ", ".join(agents))
        messages = fetch_messages(limit=20)
        print(f"messages : API OK ({len(messages)} fetched)")
        try:
            latest = load_run()
            print(f"last run : {latest.get('run_id')}  {latest.get('state')}  {latest.get('stage')}")
        except KaigiError:
            pass
    except KaigiError as exc:
        print(f"messages : {RED}API ERROR{RESET} ({exc})")
        return 1
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    ensure_server()
    status = fetch_status()
    cfgs = load_agents_config()
    names = sorted(set(status) | set(cfgs))
    if not names:
        print("agent情報なし")
        return 0
    for name in names:
        info = status.get(name, {}) if isinstance(status.get(name), dict) else {}
        cfg = cfgs.get(name, {})
        mark = "●" if info.get("available") else "○"
        role = info.get("role") or "-"
        kind = "api-local" if is_local_api(cfg) else ("api-cloud" if cfg.get("type") == "api" else "cli")
        ready, why = api_agent_readiness(name, cfg) if cfg.get("type") == "api" else (True, "")
        suffix = f"  {kind}"
        if cfg.get("type") == "api" and not ready:
            suffix += f"  [{why}]"
        print(f"{mark} {name:<16} role={role}{suffix}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    ensure_server()
    messages = fetch_messages(limit=args.count, channel=args.channel)
    print_messages(messages[-args.count:], channel=args.channel)
    return 0


def cmd_say(args: argparse.Namespace) -> int:
    ensure_server()
    text = " ".join(args.text).strip()
    if not text:
        raise KaigiError("送信する本文が空です。")
    send_message(text, args.channel)
    print(f"{GREEN}✓{RESET} 送信  {DIM}#{args.channel}{RESET}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    ensure_server()
    initial = [] if args.from_start else fetch_messages(limit=args.tail, channel=args.channel)
    print_messages(initial[-args.tail:], channel=args.channel)
    last = -1 if args.from_start else latest_id(initial, -1)
    print(f"{DIM}── live / Ctrl-Cで終了 ──{RESET}")
    try:
        while True:
            messages = fetch_messages(since_id=last, channel=args.channel)
            if messages:
                print_messages(messages, channel=args.channel)
                last = latest_id(messages, last)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


def parse_cast(items: list[str]) -> dict[str, str]:
    cast: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise KaigiError(f"--cast は role=agent 形式: {item}")
        role, agent = item.split("=", 1)
        role, agent = role.strip(), agent.strip().lstrip("@")
        if not role or not agent:
            raise KaigiError(f"--cast は role=agent 形式: {item}")
        cast[role] = agent
    return cast


def choose_cast(template: dict[str, Any], agents_arg: str | None, cast_items: list[str]) -> dict[str, str]:
    explicit = parse_cast(cast_items)
    if explicit:
        return explicit
    if not agents_arg:
        return {}
    requested = [x.strip().lstrip("@") for x in agents_arg.split(",") if x.strip()]
    roles = [str(r) for r in template.get("roles", [])]
    if not requested or not roles:
        return {}
    return {role: requested[i % len(requested)] for i, role in enumerate(roles)}


def wait_agent_round(targets: list[str], since_id: Any, channel: str, timeout: float,
                     quorum: int) -> tuple[dict[str, dict[str, Any]], Any]:
    replies: dict[str, dict[str, Any]] = {}
    target_set = set(targets)
    last = since_id
    deadline = time.time() + max(0.1, timeout)
    while time.time() < deadline:
        messages = fetch_messages(since_id=last, channel=channel)
        if messages:
            last = latest_id(messages, last)
            print_messages(messages, channel=channel)
            for msg in messages:
                sender = str(msg.get("sender") or "")
                if sender in target_set and sender not in replies:
                    replies[sender] = msg
            if len(replies) >= quorum:
                break
        time.sleep(0.35)
    return replies, last


def choose_synth(targets: list[str], requested: str | None) -> str:
    if requested:
        name = requested.lstrip("@")
        if name not in targets:
            raise KaigiError(f"synth agent が参加者にいません: {name}")
        return name
    lowered = {x.lower(): x for x in targets}
    for preferred in SYNTH_PRIORITY:
        if preferred in lowered:
            return lowered[preferred]
    return targets[0]


def run_council(args: argparse.Namespace, topic: str) -> int:
    maybe_wake_for_council(args)
    status = fetch_status()
    online = online_agents(status)
    if args.agents:
        targets = [x.strip().lstrip("@") for x in args.agents.split(",") if x.strip()]
        offline = [x for x in targets if x not in online]
        if offline:
            raise KaigiError("offline agent: " + ", ".join(offline))
    else:
        targets = list(online)
    if args.max_agents and args.max_agents > 0:
        targets = targets[:args.max_agents]
    if not targets:
        raise KaigiError("online agent がいません。kaigi agents で確認してください。")

    role_map = {agent: COUNCIL_ROLES[i % len(COUNCIL_ROLES)] for i, agent in enumerate(targets)}
    synth = choose_synth(targets, args.synth)
    run = new_run(topic, args.channel, "council", participants=targets, roles=role_map, synth=synth)
    started = time.monotonic()
    try:
        mentions = " ".join(f"@{a}" for a in targets)
        assignments = ", ".join(f"{a}={role_map[a]}" for a in targets)
        kickoff_text = (
            f"{mentions} [KAIGI COUNCIL / ROUND 1] RUN={run['run_id']} 議題: {topic}\n"
            f"役割: {assignments}\n"
            "各自は他者の回答を待たず独立に分析する。結論、根拠、主要リスク、具体策を1メッセージで返す。"
            "このラウンドでは他エージェントを@mentionしない。"
        )
        kickoff = send_message(kickoff_text, args.channel)
        kickoff_id = msg_id(kickoff) if isinstance(kickoff, dict) else latest_id(fetch_messages(limit=1), -1)
        run.update(stage="round1", kickoff_message_id=kickoff_id)
        save_run(run)
        print(f"{GREEN}✓{RESET} council開始  run={run['run_id']}  agents={len(targets)}")
        print(f"roles    : {assignments}")
        print(f"goal     : {topic}")
        if args.kickoff_only:
            run.update(state="detached", stage="round1")
            save_run(run)
            return 0

        quorum = args.quorum if args.quorum > 0 else len(targets)
        quorum = max(1, min(quorum, len(targets)))
        round1, last = wait_agent_round(targets, kickoff_id, args.channel, args.round_timeout, quorum)
        run["round1"] = {a: {"id": msg_id(m), "text": str(m.get("text", ""))} for a, m in round1.items()}
        if not round1:
            run.update(state="waiting", stage="round1_timeout")
            save_run(run)
            raise KaigiError("ROUND 1 の応答がありませんでした。run は保存済みです。")

        responders = [a for a in targets if a in round1]
        round2_mentions = " ".join(f"@{a}" for a in responders)
        round2_text = (
            f"{round2_mentions} [KAIGI COUNCIL / ROUND 2] RUN={run['run_id']} 議題: {topic}\n"
            "直前の独立案を相互批判する。最も危険な前提/見落としを1つ以上指摘し、"
            "自分の案を必要なら修正する。賛成だけで終わらせず、残る異論を明示する。"
            "このラウンドでも他エージェントを@mentionしない。"
        )
        round2_msg = send_message(round2_text, args.channel)
        round2_id = msg_id(round2_msg) if isinstance(round2_msg, dict) else last
        run.update(stage="round2", round2_message_id=round2_id, responders=responders)
        save_run(run)
        print(f"{GREEN}✓{RESET} round=2  dissent/review")
        round2, last = wait_agent_round(
            responders, round2_id, args.channel, args.round_timeout,
            max(1, min(quorum, len(responders)))
        )
        run["round2"] = {a: {"id": msg_id(m), "text": str(m.get("text", ""))} for a, m in round2.items()}

        synth = choose_synth(responders, args.synth)
        synth_text = (
            f"@{synth} [KAIGI COUNCIL / FINAL] RUN={run['run_id']} 議題: {topic}\n"
            f"ROUND1応答: {', '.join(responders)}。ROUND2応答: {', '.join(a for a in responders if a in round2) or 'なし'}。\n"
            "ここまでの全発言を比較し、単なる要約ではなく最終判断を出す。"
            "出力は DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS の順。"
            "確定事項と未確定事項を分離し、実行に移すべき最小の次手まで具体化する。"
        )
        final_msg = send_message(synth_text, args.channel)
        final_id = msg_id(final_msg) if isinstance(final_msg, dict) else last
        run.update(stage="final", synth=synth, final_request_message_id=final_id)
        save_run(run)
        print(f"{GREEN}✓{RESET} final synthesis -> @{synth}")
        final, _ = wait_agent_round([synth], final_id, args.channel, args.round_timeout, 1)
        if not final:
            run.update(state="waiting", stage="final_timeout")
            save_run(run)
            raise KaigiError(f"FINAL の応答がありませんでした: {synth}。run は保存済みです。")
        final_msg_obj = final[synth]
        final_text = str(final_msg_obj.get("text", ""))
        run.update(
            state="complete", stage="complete", final_sender=synth,
            final_message_id=msg_id(final_msg_obj), final_text=final_text,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
        save_run(run)
        print(f"{GREEN}✓{RESET} council完了  run={run['run_id']}")
        print(f"{DIM}result: kaigi result {run['run_id']}{RESET}")
        return 0
    except Exception as exc:
        if run.get("state") not in {"waiting", "detached"}:
            run_fail(run, exc)
        raise


def fallback_convene(topic: str, channel: str, agents_arg: str | None) -> None:
    if agents_arg:
        names = [x.strip().lstrip("@") for x in agents_arg.split(",") if x.strip()]
        mention = " ".join(f"@{x}" for x in names)
    else:
        mention = "@all"
    prompt = (
        f"{mention} 会議招集。議題: {topic}\n"
        "各自まず独立に分析し、他者の案に引っ張られず結論・根拠・リスクを出す。"
        "その後、相互批判して矛盾を潰し、最後に最も妥当な統合案を1つにまとめる。"
    )
    send_message(prompt, channel)


def follow_session(session_id: int, channel: str, timeout: int, run: dict[str, Any],
                   interval: float = 1.0) -> int:
    initial = fetch_messages(limit=8, channel=channel)
    last = latest_id(initial, -1)
    print_messages(initial, channel=channel)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        msgs = fetch_messages(since_id=last, channel=channel)
        if msgs:
            print_messages(msgs, channel=channel)
            last = latest_id(msgs, last)
        active = request_json("GET", "/api/sessions/active", params={"channel": channel})
        if not active or (isinstance(active, dict) and active.get("id") != session_id):
            candidates = [m for m in fetch_messages(limit=30, channel=channel)
                          if str(m.get("sender") or "") not in {"user", "system"}]
            if candidates:
                final = candidates[-1]
                run.update(final_sender=final.get("sender"), final_message_id=msg_id(final),
                           final_text=str(final.get("text", "")))
            run.update(state="complete", stage="complete")
            save_run(run)
            return 0
    run.update(state="waiting", stage="follow_timeout")
    save_run(run)
    print(f"{YELLOW}!{RESET} follow timeout。run={run['run_id']} は保存済みです。")
    return 0


def cmd_convene(args: argparse.Namespace) -> int:
    ensure_server()
    topic = " ".join(args.topic).strip()
    if not topic:
        raise KaigiError("議題が空です。")
    aliases = {"plan": "planning", "review": "code-review", "code": "code-review",
               "design": "design-critique", "swarm": "council"}
    template_id = aliases.get(args.template, args.template)
    if template_id == "council":
        return run_council(args, topic)
    run = new_run(topic, args.channel, "session", template=template_id)
    try:
        templates = fetch_templates()
        template = next((x for x in templates if x.get("id") == template_id), None)
        if not template:
            raise KaigiError(f"session template がありません: {template_id}")
        cast = choose_cast(template, args.agents, args.cast)
        payload: dict[str, Any] = {
            "template_id": template_id, "channel": args.channel,
            "goal": topic, "started_by": "user"
        }
        if cast:
            payload["cast"] = cast
        session = request_json("POST", "/api/sessions/start", payload=payload)
        if not isinstance(session, dict) or not session.get("id"):
            raise KaigiError("session start response が不正です")
        run.update(stage="running", session_id=session["id"], cast=session.get("cast", cast))
        save_run(run)
        print(f"{GREEN}✓{RESET} 会議開始  run={run['run_id']}  session={session['id']}  template={template_id}")
        if session.get("cast"):
            print("cast     : " + ", ".join(f"{r}={a}" for r, a in session["cast"].items()))
        print(f"goal     : {topic}")
        if args.no_follow or not sys.stdout.isatty():
            run.update(state="detached")
            save_run(run)
            return 0
        return follow_session(int(session["id"]), args.channel, args.timeout, run)
    except KaigiError as exc:
        if "HTTP 409" in str(exc) or not args.fallback:
            run_fail(run, exc)
            raise
        eprint(f"{YELLOW}! native session unavailable:{RESET} {exc}")
        fallback_convene(topic, args.channel, args.agents)
        run.update(state="detached", stage="fallback")
        save_run(run)
        print(f"{GREEN}✓{RESET} @mention会議として招集しました  run={run['run_id']}")
        return 0


def cmd_templates(_args: argparse.Namespace) -> int:
    ensure_server()
    for t in fetch_templates():
        print(f"{t.get('id','?'):<18} {t.get('name','')}  {t.get('description','')}")
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    run = load_run(args.run_id)
    if args.json:
        print(json.dumps(run, ensure_ascii=False, indent=2))
        return 0
    print(f"run      : {run.get('run_id')}")
    print(f"state    : {run.get('state')} / {run.get('stage')}")
    print(f"topic    : {run.get('topic')}")
    if run.get("participants"):
        print("agents   : " + ", ".join(run["participants"]))
    if run.get("synth"):
        print(f"synth    : {run['synth']}")
    final = run.get("final_text")
    if final:
        print(f"\n{BOLD}FINAL{RESET}\n{final}")
        return 0
    print(f"\n{YELLOW}最終結果はまだ保存されていません。{RESET}")
    if run.get("error"):
        print(f"error    : {run['error']}")
    return 2 if run.get("state") not in {"complete"} else 0


def cmd_history(args: argparse.Namespace) -> int:
    runs = list_runs(args.count)
    if args.json:
        print(json.dumps(runs, ensure_ascii=False, indent=2))
        return 0
    if not runs:
        print("保存済み会議なし")
        return 0
    for run in runs:
        started = str(run.get("started_at", ""))
        when = started[5:16].replace("T", " ") if len(started) >= 16 else started
        topic = str(run.get("topic", "")).replace("\n", " ")
        if len(topic) > 60:
            topic = topic[:57] + "..."
        print(f"{run.get('run_id','?'):<24} {run.get('state','?'):<9} {when:<11} {topic}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    run = load_run(args.run_id)
    if args.format == "json":
        content = json.dumps(run, ensure_ascii=False, indent=2) + "\n"
    else:
        lines = [
            f"# KAIGI {run.get('run_id')}",
            "",
            f"- State: {run.get('state')} / {run.get('stage')}",
            f"- Topic: {run.get('topic')}",
            f"- Started: {run.get('started_at')}",
        ]
        if run.get("participants"):
            lines.append(f"- Participants: {', '.join(run['participants'])}")
        if run.get("synth"):
            lines.append(f"- Synthesizer: {run['synth']}")
        lines.extend(["", "## Final", "", str(run.get("final_text") or "(not available)")])
        content = "\n".join(lines) + "\n"
    if args.output:
        path = pathlib.Path(args.output).expanduser()
    else:
        ext = "json" if args.format == "json" else "md"
        path = pathlib.Path.cwd() / f"kaigi-{run['run_id']}.{ext}"
    path.write_text(content, encoding="utf-8")
    print(path)
    return 0


def managed_api_block(name: str, *, model: str, base_url: str, label: str,
                      api_key_env: str, context_messages: int = 30) -> str:
    key_line = f'api_key_env = {json.dumps(api_key_env)}\n' if api_key_env else ""
    return (
        f"# BEGIN KAIGI API {name}\n"
        f"[agents.{name}]\n"
        "type = \"api\"\n"
        f"base_url = {json.dumps(base_url)}\n"
        f"model = {json.dumps(model)}\n"
        f"label = {json.dumps(label)}\n"
        f"{key_line}"
        f"context_messages = {int(context_messages)}\n"
        f"# END KAIGI API {name}\n"
    )


def install_api_config(name: str, model: str, base_url: str, label: str,
                       api_key_env: str, context_messages: int = 30) -> pathlib.Path:
    HOME.mkdir(parents=True, exist_ok=True)
    path = HOME / "config.local.toml"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = f"# BEGIN KAIGI API {name}", f"# END KAIGI API {name}"
    block = managed_api_block(
        name, model=model, base_url=base_url, label=label,
        api_key_env=api_key_env, context_messages=context_messages
    )
    if start in current and end in current:
        before = current.split(start, 1)[0].rstrip()
        after = current.split(end, 1)[1].lstrip("\n")
        current = (before + "\n\n" if before else "") + block + ("\n" + after if after else "")
    else:
        current = current.rstrip() + ("\n\n" if current.strip() else "") + block
    path.write_text(current, encoding="utf-8")
    return path


def cmd_api(args: argparse.Namespace) -> int:
    if args.api_command == "add":
        path = install_api_config(
            args.name, args.model, args.base_url,
            args.label or args.name, args.api_key_env or "", args.context_messages
        )
        print(f"{GREEN}✓{RESET} API agent設定: {args.name} -> {path}")
        if args.api_key_env and not os.environ.get(args.api_key_env):
            print(f"{YELLOW}!{RESET} {args.api_key_env} は未設定")
        print("設定を追加/変更した場合は agentchattr を再起動してから `kaigi api start NAME`")
        return 0
    if args.api_command == "start":
        cfgs = load_agents_config()
        if args.all:
            names = [n for n, cfg in cfgs.items() if cfg.get("type") == "api"]
        else:
            names = args.names
        if not names:
            raise KaigiError("起動するAPI agentを指定してください。")
        results = start_api_agents(names, wait=args.wait, include_cloud=args.cloud or bool(args.names))
        bad = [v for v in results.values() if v not in {"online", "running"} and not v.startswith("started:")]
        return 1 if bad else 0
    if args.api_command == "list":
        cfgs = load_agents_config()
        status = fetch_status() if server_alive() else {}
        for name, cfg in sorted(cfgs.items()):
            if cfg.get("type") != "api":
                continue
            online = bool(isinstance(status.get(name), dict) and status[name].get("available"))
            ready, why = api_agent_readiness(name, cfg)
            kind = "local" if is_local_api(cfg) else "cloud"
            print(f"{'●' if online else '○'} {name:<16} {kind:<5} {cfg.get('model','-'):<24} {'ready' if ready else why}")
        return 0
    raise KaigiError("api subcommand が必要です: add/start/list")


def cmd_chatgpt(args: argparse.Namespace) -> int:
    if args.chat_command == "setup":
        path = install_api_config(
            args.name, args.model, args.base_url, "ChatGPT", "OPENAI_API_KEY", 30
        )
        print(f"{GREEN}✓{RESET} ChatGPT agent設定を追加: {path}")
        if os.environ.get("OPENAI_API_KEY"):
            print(f"{GREEN}✓{RESET} OPENAI_API_KEY 検出")
        else:
            print(f"{YELLOW}!{RESET} OPENAI_API_KEY は未設定。")
        print("次: agentchattr再起動後 `kaigi chatgpt start`")
        return 0
    if args.chat_command == "start":
        results = start_api_agents([args.name], wait=args.wait, include_cloud=True)
        return 0 if results.get(args.name) in {"online", "running"} or str(results.get(args.name)).startswith("started:") else 1
    if args.chat_command == "status":
        ensure_server()
        status = fetch_status()
        info = status.get(args.name, {}) if isinstance(status, dict) else {}
        online = bool(isinstance(info, dict) and info.get("available"))
        cfg = load_agents_config().get(args.name, {})
        ready, why = api_agent_readiness(args.name, cfg)
        print(f"chatgpt  : {'ONLINE' if online else 'OFFLINE'} ({args.name})")
        print(f"api key  : {'set' if os.environ.get('OPENAI_API_KEY') else 'not set'}")
        print(f"model    : {cfg.get('model') or args.model}")
        print(f"ready    : {'yes' if ready else why}")
        return 0 if online else 1
    raise KaigiError("chatgpt subcommand が必要です: setup/start/status")


def cmd_wake(args: argparse.Namespace) -> int:
    cfgs = load_agents_config()
    if args.all_api:
        names = [n for n, cfg in cfgs.items() if cfg.get("type") == "api"]
    elif args.names:
        names = args.names
    else:
        names = [n for n, cfg in cfgs.items() if is_local_api(cfg)]
    if not names:
        print("起動対象のAPI agentなし")
        return 0
    results = start_api_agents(names, wait=args.wait, include_cloud=args.cloud or bool(args.names))
    bad = [v for v in results.values() if v not in {"online", "running", "cloud-skipped"} and not v.startswith("started:")]
    return 1 if bad else 0


def cmd_room(args: argparse.Namespace) -> int:
    ensure_server()
    initial = fetch_messages(limit=args.tail, channel=args.channel)
    print(f"{BOLD}kaigi{RESET}  {DIM}{SERVER_URL}  #{args.channel}{RESET}")
    print_messages(initial[-args.tail:], channel=args.channel)
    print(f"{DIM}── Enter=送信 / /convene / /result / /agents / /help / /quit ──{RESET}")
    last_box = [latest_id(initial, -1)]
    stop = threading.Event()
    out_lock = threading.Lock()

    def watcher() -> None:
        while not stop.wait(args.interval):
            try:
                messages = fetch_messages(since_id=last_box[0], channel=args.channel)
            except KaigiError:
                continue
            if not messages:
                continue
            last_box[0] = latest_id(messages, last_box[0])
            with out_lock:
                print()
                print_messages(messages, channel=args.channel)

    thread = threading.Thread(target=watcher, daemon=True)
    thread.start()
    try:
        while True:
            try:
                line = input(f"{CYAN}you ›{RESET} ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line in {"/q", "/quit", "/exit"}:
                break
            if line == "/help":
                print("/quit | /status | /agents | /log [N] | /to NAME TEXT | /convene TOPIC | /result | /history")
                continue
            if line == "/status":
                cmd_status(args)
                continue
            if line == "/agents":
                cmd_agents(args)
                continue
            if line == "/result":
                try:
                    cmd_result(argparse.Namespace(run_id="latest", json=False))
                except KaigiError as exc:
                    eprint(exc)
                continue
            if line == "/history":
                cmd_history(argparse.Namespace(count=10, json=False))
                continue
            if line.startswith("/log"):
                parts = line.split()
                n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
                print_messages(fetch_messages(limit=n, channel=args.channel)[-n:], channel=args.channel)
                continue
            if line.startswith("/to "):
                parts = line.split(maxsplit=2)
                if len(parts) < 3:
                    print("使い方: /to claude これ見て")
                    continue
                line = f"@{parts[1].lstrip('@')} {parts[2]}"
            if line.startswith("/convene "):
                ns = argparse.Namespace(
                    topic=[line[len("/convene "):]], template="council", agents=None, cast=[],
                    channel=args.channel, no_follow=True, timeout=180, fallback=True,
                    round_timeout=60.0, quorum=0, max_agents=0, synth=None,
                    kickoff_only=False, no_wake=False, wake_timeout=5.0
                )
                try:
                    cmd_convene(ns)
                except KaigiError as exc:
                    eprint(f"{RED}会議失敗:{RESET} {exc}")
                continue
            try:
                send_message(line, args.channel)
            except KaigiError as exc:
                eprint(f"{RED}送信失敗:{RESET} {exc}")
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        thread.join(timeout=1.5)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    start_server(wrapper="" if args.no_wrapper else args.wrapper, quiet=False)
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    if stop_server():
        print(f"{GREEN}✓{RESET} kaigi管理プロセスを停止しました")
        return 0
    if server_alive():
        print(f"{YELLOW}!{RESET} サーバーは動作中ですがkaigi管理PIDではないため停止していません。")
        return 1
    print("agentchattr は停止中です。")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    stop_server()
    time.sleep(0.3)
    start_server(wrapper="" if args.no_wrapper else args.wrapper, quiet=False)
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks = [
        ("agentchattr home", HOME.is_dir(), str(HOME)),
        ("run.py", (HOME / "run.py").is_file(), str(HOME / "run.py")),
        ("python venv", python_bin() is not None, str(python_bin() or "not found")),
        ("wrapper_api.py", (HOME / "wrapper_api.py").is_file(), str(HOME / "wrapper_api.py")),
        ("auth token", resolve_token()[0] is not None, resolve_token()[1]),
        ("server", server_alive(), SERVER_URL),
        ("state dir", True, str(STATE_DIR)),
    ]
    if server_alive():
        for name, path in (("messages API", "/api/messages"), ("status API", "/api/status"),
                           ("sessions API", "/api/sessions/templates")):
            try:
                request_json("GET", path, params={"limit": 1} if path == "/api/messages" else None)
                checks.append((name, True, path + " OK"))
            except Exception as exc:
                checks.append((name, False, str(exc)))
    print(f"kaigi {VERSION}\n")
    for name, ok, detail in checks:
        print(f"{GREEN+'✓'+RESET if ok else RED+'✗'+RESET} {name:<16} {detail}")
    return 0 if all(x[1] for x in checks if x[0] in {
        "agentchattr home", "run.py", "python venv", "server", "messages API"
    }) else 1


def cmd_open(_args: argparse.Namespace) -> int:
    ensure_server()
    for cmd, binary in [
        (["wslview", SERVER_URL], "wslview"),
        (["xdg-open", SERVER_URL], "xdg-open"),
        (["open", SERVER_URL], "open"),
        (["cmd.exe", "/c", "start", "", SERVER_URL], "cmd.exe"),
    ]:
        if shutil.which(binary):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{GREEN}✓{RESET} ブラウザで開きました: {SERVER_URL}")
            return 0
    print(SERVER_URL)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kaigi", description="agentchattr multi-AI council CLI")
    p.add_argument("--version", action="version", version=f"kaigi {VERSION}")
    sub = p.add_subparsers(dest="command")

    room = sub.add_parser("room", aliases=["join"], help="会議室に入る（既定）")
    room.add_argument("--tail", type=int, default=20)
    room.add_argument("--channel", "-c", default=DEFAULT_CHANNEL)
    room.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    room.set_defaults(func=cmd_room)

    log = sub.add_parser("log", aliases=["l"], help="履歴")
    log.add_argument("count", type=int, nargs="?", default=20)
    log.add_argument("--channel", "-c", default=None)
    log.set_defaults(func=cmd_log)

    watch = sub.add_parser("watch", aliases=["w"], help="ライブ監視")
    watch.add_argument("--tail", type=int, default=10)
    watch.add_argument("--channel", "-c", default=None)
    watch.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    watch.add_argument("--from-start", action="store_true")
    watch.set_defaults(func=cmd_watch)

    say = sub.add_parser("say", aliases=["s"], help="発言")
    say.add_argument("text", nargs="+")
    say.add_argument("--channel", "-c", default=DEFAULT_CHANNEL)
    say.set_defaults(func=cmd_say)

    status = sub.add_parser("status", aliases=["st", "who"], help="状態")
    status.set_defaults(func=cmd_status)

    agents = sub.add_parser("agents", aliases=["a"], help="参加/設定エージェント")
    agents.set_defaults(func=cmd_agents)

    conv = sub.add_parser("convene", aliases=["meet", "c"], help="AI会議を1コマンド招集")
    conv.add_argument("topic", nargs="+")
    conv.add_argument("--template", "-t", default="council",
                      help="council(default)/planning/debate/code-review/design-critique")
    conv.add_argument("--agents", help="claude,codex,chatgpt のように指定")
    conv.add_argument("--cast", action="append", default=[], help="role=agent（複数可）")
    conv.add_argument("--channel", "-c", default=DEFAULT_CHANNEL)
    conv.add_argument("--no-follow", action="store_true")
    conv.add_argument("--timeout", type=int, default=180)
    conv.add_argument("--round-timeout", type=float, default=60.0)
    conv.add_argument("--quorum", type=int, default=0, help="0=全員")
    conv.add_argument("--max-agents", type=int, default=0, help="0=online全員")
    conv.add_argument("--synth", help="最終統合担当")
    conv.add_argument("--kickoff-only", action="store_true")
    conv.add_argument("--no-wake", action="store_true", help="local API agentの自動起床を無効化")
    conv.add_argument("--wake-timeout", type=float, default=5.0)
    conv.add_argument("--no-fallback", dest="fallback", action="store_false", default=True)
    conv.set_defaults(func=cmd_convene)

    templates = sub.add_parser("templates", aliases=["tpl"], help="会議テンプレ一覧")
    templates.set_defaults(func=cmd_templates)

    result = sub.add_parser("result", aliases=["res"], help="保存済み最終結論")
    result.add_argument("run_id", nargs="?", default="latest")
    result.add_argument("--json", action="store_true")
    result.set_defaults(func=cmd_result)

    history = sub.add_parser("history", aliases=["hist"], help="会議run履歴")
    history.add_argument("count", type=int, nargs="?", default=20)
    history.add_argument("--json", action="store_true")
    history.set_defaults(func=cmd_history)

    export = sub.add_parser("export", help="会議結果をMarkdown/JSONへ出力")
    export.add_argument("run_id", nargs="?", default="latest")
    export.add_argument("--format", choices=["md", "json"], default="md")
    export.add_argument("--output", "-o")
    export.set_defaults(func=cmd_export)

    wake = sub.add_parser("wake", help="API agentを起床")
    wake.add_argument("names", nargs="*")
    wake.add_argument("--all-api", action="store_true")
    wake.add_argument("--cloud", action="store_true", help="--all-api時にcloud APIも起動")
    wake.add_argument("--wait", type=float, default=5.0)
    wake.set_defaults(func=cmd_wake)

    api = sub.add_parser("api", help="OpenAI互換API agentを追加/起動")
    asub = api.add_subparsers(dest="api_command")
    add = asub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--base-url", required=True)
    add.add_argument("--model", required=True)
    add.add_argument("--label")
    add.add_argument("--api-key-env")
    add.add_argument("--context-messages", type=int, default=30)
    add.set_defaults(func=cmd_api)
    ast = asub.add_parser("start")
    ast.add_argument("names", nargs="*")
    ast.add_argument("--all", action="store_true")
    ast.add_argument("--cloud", action="store_true")
    ast.add_argument("--wait", type=float, default=5.0)
    ast.set_defaults(func=cmd_api)
    al = asub.add_parser("list")
    al.set_defaults(func=cmd_api)

    chat = sub.add_parser("chatgpt", aliases=["chat"], help="ChatGPTを会議参加者として設定/起動")
    csub = chat.add_subparsers(dest="chat_command")
    setup = csub.add_parser("setup")
    setup.add_argument("--name", default="chatgpt")
    setup.add_argument("--model", default=DEFAULT_CHATGPT_MODEL)
    setup.add_argument("--base-url", default="https://api.openai.com/v1")
    setup.set_defaults(func=cmd_chatgpt)
    cstart = csub.add_parser("start")
    cstart.add_argument("--name", default="chatgpt")
    cstart.add_argument("--model", default=DEFAULT_CHATGPT_MODEL)
    cstart.add_argument("--wait", type=float, default=5.0)
    cstart.set_defaults(func=cmd_chatgpt)
    cstatus = csub.add_parser("status")
    cstatus.add_argument("--name", default="chatgpt")
    cstatus.add_argument("--model", default=DEFAULT_CHATGPT_MODEL)
    cstatus.set_defaults(func=cmd_chatgpt)

    start = sub.add_parser("start", help="サーバー起動")
    start.add_argument("--wrapper", default=DEFAULT_WRAPPER)
    start.add_argument("--no-wrapper", action="store_true")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="kaigi管理サーバー停止")
    stop.set_defaults(func=cmd_stop)

    restart = sub.add_parser("restart", aliases=["r"], help="再起動")
    restart.add_argument("--wrapper", default=DEFAULT_WRAPPER)
    restart.add_argument("--no-wrapper", action="store_true")
    restart.set_defaults(func=cmd_restart)

    doctor = sub.add_parser("doctor", aliases=["d"], help="環境診断")
    doctor.set_defaults(func=cmd_doctor)

    op = sub.add_parser("open", help="Web UI")
    op.set_defaults(func=cmd_open)

    help_p = sub.add_parser("help", help="ヘルプ")
    help_p.set_defaults(func=lambda _a: (p.print_help() or 0))
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {
        "room", "join", "log", "l", "watch", "w", "say", "s", "status", "st", "who",
        "agents", "a", "convene", "meet", "c", "templates", "tpl", "result", "res",
        "history", "hist", "export", "wake", "api", "chatgpt", "chat", "start", "stop",
        "restart", "r", "doctor", "d", "open", "help"
    }
    if not argv:
        argv = ["room"]
    elif argv[0] not in known and not argv[0].startswith("-"):
        argv = ["say", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return int(args.func(args) or 0)
    except KaigiError as exc:
        eprint(f"{RED}エラー:{RESET} {exc}")
        return 1
    except KeyboardInterrupt:
        print()
        return 130
