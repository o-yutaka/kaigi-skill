#!/usr/bin/env python3
"""kaigi v8 outbound relay worker.

The relay never exposes the local agentchattr server. It polls an authenticated
HTTPS endpoint, executes only an allowlisted advisory Council operation, verifies
the local Decision packet, and returns only the final decision + proof hashes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import kaigi_core as core
import kaigi_v6 as v6

VERSION = "8.0.0"
CONFIG_PATH = pathlib.Path(os.environ.get(
    "KAIGI_RELAY_CONFIG", str(pathlib.Path.home() / ".config/kaigi/relay.json")
)).expanduser()
RECEIPTS_DIR = core.STATE_DIR / "relay-receipts"
EXEC_LOG_DIR = core.STATE_DIR / "relay-exec"
PID_FILE = core.STATE_DIR / "relay.pid"
DAEMON_LOG = core.STATE_DIR / "relay.log"
DEFAULT_INTERVAL = float(os.environ.get("KAIGI_RELAY_INTERVAL", "3"))
DEFAULT_LEASE = int(os.environ.get("KAIGI_RELAY_LEASE_SECONDS", "300"))
DEFAULT_MAX_EXECUTION = int(os.environ.get("KAIGI_RELAY_MAX_EXECUTION_SECONDS", "1800"))
RECEIPT_SCHEMA = "kaigi.relay_receipt.v1"
ALLOWED_OPTIONS = {
    "need", "prefer", "free_only", "allow_cloud", "max_agents", "min_agents",
    "round_timeout", "quorum", "best_effort_capabilities", "no_launch",
}


class RelayError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _atomic_json(path: pathlib.Path, data: dict[str, Any], mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode is not None:
        try:
            tmp.chmod(mode)
        except OSError:
            pass
    os.replace(tmp, path)
    if mode is not None:
        try:
            path.chmod(mode)
        except OSError:
            pass


def _safe_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return value
    raise RelayError("relay URLはHTTPS（またはloopback HTTP）だけ許可します。")


def load_config(required: bool = True) -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        if required:
            raise RelayError(f"relay未pairです: {CONFIG_PATH}\n先に kaigi relay pair --url URL")
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RelayError(f"relay configを読めません: {exc}") from exc
    if not isinstance(data, dict):
        raise RelayError("relay configが不正です。")
    if required:
        for key in ("url", "worker_id", "token"):
            if not data.get(key):
                raise RelayError(f"relay configに{key}がありません。")
        data["url"] = _safe_url(str(data["url"]))
    return data


def save_config(data: dict[str, Any]) -> None:
    out = dict(data)
    out["updated_at"] = dt.datetime.now().astimezone().isoformat()
    _atomic_json(CONFIG_PATH, out, mode=0o600)


def _http_json(url: str, payload: dict[str, Any], *, token: str | None = None,
               timeout: float = 15.0) -> dict[str, Any]:
    headers = {"accept": "application/json", "content-type": "application/json"}
    if token:
        headers["x-kaigi-worker-token"] = token
    req = urllib.request.Request(
        _safe_url(url), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RelayError(f"relay HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RelayError(f"relayへ接続できません: {exc.reason}") from exc
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RelayError("relay応答がJSONではありません。") from exc
    if not isinstance(data, dict):
        raise RelayError("relay応答が不正です。")
    if data.get("error"):
        raise RelayError(f"relay error: {data.get('error')} {data.get('detail') or ''}".strip())
    return data


class RelayClient:
    def __init__(self, config: dict[str, Any]):
        self.url = _safe_url(str(config["url"]))
        self.token = str(config["token"])
        self.worker_id = str(config["worker_id"])

    def action(self, action: str, **payload: Any) -> dict[str, Any]:
        return _http_json(self.url, {"action": action, **payload}, token=self.token)

    def ping(self) -> dict[str, Any]:
        return self.action("ping")

    def claim(self, lease_seconds: int) -> dict[str, Any] | None:
        data = self.action("claim", lease_seconds=lease_seconds)
        req = data.get("request")
        if isinstance(req, list):
            req = req[0] if req else None
        return req if isinstance(req, dict) else None


def _request_hash(req: dict[str, Any]) -> str:
    return _sha({
        "id": str(req.get("id") or ""),
        "topic": str(req.get("topic") or ""),
        "options": req.get("options") if isinstance(req.get("options"), dict) else {},
        "requested_by": str(req.get("requested_by") or ""),
    })


def _receipt_path(request_id: str) -> pathlib.Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", request_id)
    return RECEIPTS_DIR / f"{safe}.json"


def load_receipt(request_id: str) -> dict[str, Any] | None:
    path = _receipt_path(request_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema") != RECEIPT_SCHEMA:
        return None
    return data


def save_receipt(req: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    run = packet.get("run") if isinstance(packet.get("run"), dict) else {}
    decision = packet.get("decision") if isinstance(packet.get("decision"), dict) else {}
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "request_id": str(req["id"]),
        "request_sha256": _request_hash(req),
        "run_id": str(run.get("run_id") or ""),
        "result_text": str(decision.get("text") or ""),
        "packet_sha256": str(packet.get("packet_sha256") or ""),
        "transcript_sha256": str(packet.get("transcript_sha256") or ""),
        "created_at": dt.datetime.now().astimezone().isoformat(),
    }
    if not re.fullmatch(r"[0-9a-f]{64}", receipt["packet_sha256"]):
        raise RelayError("Decision packet hashが不正です。")
    if not re.fullmatch(r"[0-9a-f]{64}", receipt["transcript_sha256"]):
        raise RelayError("transcript hashが不正です。")
    _atomic_json(_receipt_path(str(req["id"])), receipt, mode=0o600)
    return receipt


def _complete_from_receipt(client: RelayClient, req: dict[str, Any], receipt: dict[str, Any]) -> None:
    if receipt.get("request_sha256") != _request_hash(req):
        raise RelayError("local receiptとremote requestが一致しません。")
    client.action(
        "complete",
        id=str(req["id"]), claim_token=str(req["claim_token"]),
        run_id=str(receipt.get("run_id") or ""),
        result_text=str(receipt.get("result_text") or ""),
        packet_sha256=str(receipt.get("packet_sha256") or ""),
        transcript_sha256=str(receipt.get("transcript_sha256") or ""),
    )


def _as_caps(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        items = [x.strip() for x in str(value).split(",") if x.strip()]
    for item in items:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item):
            raise RelayError(f"不正なcapability: {item}")
    return ",".join(items)


def validate_options(raw: Any, config: dict[str, Any]) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RelayError("request optionsはobjectである必要があります。")
    unknown = sorted(set(raw) - ALLOWED_OPTIONS)
    if unknown:
        raise RelayError("remote optionは許可されていません: " + ", ".join(unknown))
    out: dict[str, Any] = {}
    for key in ("need", "prefer"):
        value = _as_caps(raw.get(key))
        if value:
            out[key] = value
    for key in ("free_only", "best_effort_capabilities", "no_launch"):
        if key in raw:
            if not isinstance(raw[key], bool):
                raise RelayError(f"{key}はbooleanである必要があります。")
            out[key] = raw[key]
    if raw.get("allow_cloud"):
        if not bool(config.get("allow_remote_cloud", False)):
            raise RelayError("remote cloud利用はlocal policyで無効です。kaigi relay config --allow-remote-cloud が必要です。")
        out["allow_cloud"] = True
    for key, lo, hi in (("max_agents", 1, 8), ("min_agents", 1, 8), ("quorum", 0, 8)):
        if key in raw:
            try:
                value = int(raw[key])
            except (TypeError, ValueError) as exc:
                raise RelayError(f"{key}は整数である必要があります。") from exc
            if not lo <= value <= hi:
                raise RelayError(f"{key}は{lo}..{hi}で指定してください。")
            out[key] = value
    if "round_timeout" in raw:
        try:
            value = float(raw["round_timeout"])
        except (TypeError, ValueError) as exc:
            raise RelayError("round_timeoutは数値である必要があります。") from exc
        if not 5 <= value <= 900:
            raise RelayError("round_timeoutは5..900秒で指定してください。")
        out["round_timeout"] = value
    if out.get("min_agents", 1) > out.get("max_agents", 8):
        raise RelayError("min_agentsがmax_agentsを超えています。")
    return out


def build_decide_argv(topic: str, options: dict[str, Any]) -> list[str]:
    argv = ["decide", topic]
    if options.get("need"):
        argv += ["--need", str(options["need"])]
    if options.get("prefer"):
        argv += ["--prefer", str(options["prefer"])]
    for key, flag in (
        ("free_only", "--free-only"),
        ("allow_cloud", "--allow-cloud"),
        ("best_effort_capabilities", "--best-effort-capabilities"),
        ("no_launch", "--no-launch"),
    ):
        if options.get(key):
            argv.append(flag)
    for key, flag in (
        ("max_agents", "--max-agents"),
        ("min_agents", "--min-agents"),
        ("quorum", "--quorum"),
        ("round_timeout", "--round-timeout"),
    ):
        if key in options:
            argv += [flag, str(options[key])]
    return argv


def _run_ids() -> set[str]:
    return {str(run.get("run_id")) for run in core.list_runs(limit=1000) if run.get("run_id")}


def find_bound_run(request_id: str) -> dict[str, Any] | None:
    for run in core.list_runs(limit=1000):
        if str(run.get("relay_request_id") or "") == request_id:
            return run
    return None


def _bind_new_run(req: dict[str, Any], before: set[str]) -> dict[str, Any] | None:
    topic = str(req.get("topic") or "")
    candidates = [
        run for run in core.list_runs(limit=50)
        if str(run.get("run_id") or "") not in before and str(run.get("topic") or "") == topic
    ]
    if not candidates:
        return None
    run = candidates[0]
    if run.get("relay_request_id") and run.get("relay_request_id") != str(req["id"]):
        return None
    run["relay_request_id"] = str(req["id"])
    run["relay_request_sha256"] = _request_hash(req)
    core.save_run(run)
    return run


def _verified_packet(run_id: str) -> dict[str, Any]:
    try:
        _, packet = v6.load_packet(run_id)
    except core.KaigiError:
        run = core.load_run(run_id)
        if run.get("state") != "complete":
            raise RelayError(f"run未完了: {run_id} state={run.get('state')}")
        core.ensure_server()
        packet = v6.build_packet(run)
    errors = v6.verify_packet(packet, live=False)
    if errors:
        raise RelayError("Decision packet verify失敗: " + "; ".join(errors))
    return packet


def _exec_kaigi(req: dict[str, Any], options: dict[str, Any], max_execution: int) -> dict[str, Any]:
    request_id = str(req["id"])
    bound = find_bound_run(request_id)
    if bound:
        if bound.get("state") == "complete":
            return _verified_packet(str(bound["run_id"]))
        argv = ["recover", str(bound["run_id"]), "--round-timeout", str(options.get("round_timeout", 60))]
    else:
        argv = build_decide_argv(str(req["topic"]), options)

    before = _run_ids()
    EXEC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = EXEC_LOG_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", request_id) + ".log")
    env = os.environ.copy()
    env["KAIGI_RELAY_REQUEST_ID"] = request_id
    cli = pathlib.Path(__file__).resolve().with_name("kaigi")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{dt.datetime.now().astimezone().isoformat()}] argv={json.dumps(argv, ensure_ascii=False)}\n")
        log.flush()
        proc = subprocess.Popen([sys.executable, str(cli), *argv], stdout=log, stderr=subprocess.STDOUT, env=env)
        deadline = time.monotonic() + max(30, max_execution)
        discovered: dict[str, Any] | None = bound
        while proc.poll() is None:
            if not discovered:
                discovered = _bind_new_run(req, before)
            if time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RelayError(f"local meeting execution timeout ({max_execution}s)")
            time.sleep(0.2)
        code = int(proc.returncode or 0)
    if not bound:
        bound = find_bound_run(request_id) or _bind_new_run(req, before)
    if code != 0:
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
        except OSError:
            tail = ""
        run_hint = f" run={bound.get('run_id')}" if bound else ""
        raise RelayError(f"kaigi exited {code}.{run_hint}\n{tail}".strip())
    if not bound:
        raise RelayError("relay requestに対応するrunを特定できません。")
    return _verified_packet(str(bound["run_id"]))


class Heartbeat:
    def __init__(self, client: RelayClient, req: dict[str, Any], lease: int):
        self.client, self.req, self.lease = client, req, lease
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="kaigi-relay-heartbeat", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _loop(self) -> None:
        interval = max(10, min(60, self.lease // 3))
        while not self.stop_event.wait(interval):
            try:
                self.client.action(
                    "heartbeat", id=str(self.req["id"]), claim_token=str(self.req["claim_token"]),
                    lease_seconds=self.lease,
                )
            except Exception as exc:
                core.eprint(f"relay heartbeat warning: {exc}")


def process_request(client: RelayClient, config: dict[str, Any], req: dict[str, Any]) -> int:
    for key in ("id", "claim_token", "topic"):
        if not req.get(key):
            raise RelayError(f"claimed requestに{key}がありません。")
    topic = str(req["topic"]).strip()
    if not topic or len(topic) > 20000:
        raise RelayError("topicが空または長すぎます。")
    options = validate_options(req.get("options"), config)
    lease = max(30, min(3600, int(config.get("lease_seconds", DEFAULT_LEASE))))
    max_execution = max(30, min(21600, int(config.get("max_execution_seconds", DEFAULT_MAX_EXECUTION))))

    client.action("start", id=str(req["id"]), claim_token=str(req["claim_token"]), lease_seconds=lease)
    heartbeat = Heartbeat(client, req, lease)
    heartbeat.start()
    try:
        receipt = load_receipt(str(req["id"]))
        if receipt:
            _complete_from_receipt(client, req, receipt)
            print(f"✓ relay replay-complete request={req['id']} run={receipt.get('run_id')}")
            return 0
        packet = _exec_kaigi(req, options, max_execution)
        receipt = save_receipt(req, packet)
        _complete_from_receipt(client, req, receipt)
        print(f"✓ relay complete request={req['id']} run={receipt['run_id']} sha256={receipt['packet_sha256']}")
        return 0
    finally:
        heartbeat.stop()


def claim_and_process(config: dict[str, Any]) -> int:
    client = RelayClient(config)
    lease = max(30, min(3600, int(config.get("lease_seconds", DEFAULT_LEASE))))
    req = client.claim(lease)
    if not req:
        print("relay: no pending request")
        return 0
    print(f"▶ relay request={req.get('id')} topic={str(req.get('topic') or '')[:120]}")
    try:
        return process_request(client, config, req)
    except Exception as exc:
        try:
            if req.get("id") and req.get("claim_token"):
                client.action("fail", id=str(req["id"]), claim_token=str(req["claim_token"]), error=str(exc)[:8000])
        except Exception as report_exc:
            core.eprint(f"relay failure report warning: {report_exc}")
        raise


def cmd_pair(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("KAIGI_RELAY_URL")
    if not url:
        raise RelayError("--url または KAIGI_RELAY_URL が必要です。")
    url = _safe_url(url)
    code = args.code or getpass.getpass("Pair code: ").strip()
    if not code:
        raise RelayError("pair codeが空です。")
    name = args.name or f"{socket.gethostname()}-{os.getpid()}"
    data = _http_json(url, {"action": "pair", "code": code, "worker_name": name})
    token = str(data.get("token") or "")
    worker_id = str(data.get("worker_id") or "")
    if not token or not worker_id:
        raise RelayError("pair応答にworker credentialがありません。")
    config = load_config(required=False)
    config.update({
        "url": url, "worker_id": worker_id, "worker_name": str(data.get("worker_name") or name),
        "token": token, "allow_remote_cloud": bool(config.get("allow_remote_cloud", False)),
        "lease_seconds": int(config.get("lease_seconds", DEFAULT_LEASE)),
        "max_execution_seconds": int(config.get("max_execution_seconds", DEFAULT_MAX_EXECUTION)),
        "paired_at": dt.datetime.now().astimezone().isoformat(),
    })
    save_config(config)
    ping = RelayClient(config).ping()
    print(f"✓ paired worker={config['worker_name']} id={worker_id}")
    print(f"  relay={url}")
    print(f"  auth={ping.get('auth', 'worker')} token=stored-local-only mode=0600")
    return 0


def cmd_ping(args: argparse.Namespace) -> int:
    data = RelayClient(load_config()).ping()
    print(f"✓ relay online protocol={data.get('protocol')} worker={data.get('worker_id')}")
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    return claim_and_process(load_config())


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config()
    interval = max(0.5, float(args.interval or config.get("interval", DEFAULT_INTERVAL)))
    print(f"kaigi relay serve  interval={interval}s  worker={config.get('worker_name') or config.get('worker_id')}")
    failures = 0
    while True:
        try:
            client = RelayClient(config)
            lease = max(30, min(3600, int(config.get("lease_seconds", DEFAULT_LEASE))))
            req = client.claim(lease)
            if req:
                print(f"▶ relay request={req.get('id')} topic={str(req.get('topic') or '')[:120]}")
                process_request(client, config, req)
            failures = 0
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nrelay stopped")
            return 0
        except Exception as exc:
            failures += 1
            core.eprint(f"relay warning: {exc}")
            time.sleep(min(30.0, interval * (2 ** min(failures, 4))))


def _pid_command(pid: int) -> str:
    cmd = core.process_cmdline(pid)
    if cmd:
        return cmd
    ps = subprocess.run(["ps", "-p", str(pid), "-o", "command="], text=True, capture_output=True)
    return ps.stdout.strip() if ps.returncode == 0 else ""


def _daemon_pid() -> int | None:
    pid = core.read_pid(PID_FILE)
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return None
    cmd = _pid_command(pid)
    if "kaigi_relay.py" not in cmd or "serve" not in cmd:
        return None
    return pid


def cmd_start(args: argparse.Namespace) -> int:
    load_config()
    existing = _daemon_pid()
    if existing:
        print(f"✓ relay already running pid={existing}")
        return 0
    core.ensure_state_dirs()
    DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)
    module = pathlib.Path(__file__).resolve()
    command = [sys.executable, str(module), "serve", "--interval", str(args.interval)]
    with DAEMON_LOG.open("a", encoding="utf-8") as log:
        kwargs: dict[str, Any] = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.3)
    if not _daemon_pid():
        raise RelayError(f"relay daemon起動を確認できません。log={DAEMON_LOG}")
    print(f"✓ relay daemon started pid={proc.pid} log={DAEMON_LOG}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    pid = _daemon_pid()
    if not pid:
        print("relay daemon is not running")
        return 0
    cmd = _pid_command(pid)
    if "kaigi_relay.py" not in cmd or "serve" not in cmd:
        raise RelayError("PIDの実体がrelayではないため停止しません。")
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    PID_FILE.unlink(missing_ok=True)
    print(f"✓ relay daemon stopped pid={pid}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(required=False)
    pid = _daemon_pid()
    print(f"daemon : {'UP pid=' + str(pid) if pid else 'DOWN'}")
    print(f"config : {CONFIG_PATH if config else 'not paired'}")
    if config:
        print(f"worker : {config.get('worker_name') or config.get('worker_id')}")
        print(f"cloud  : {'allowed' if config.get('allow_remote_cloud') else 'denied'}")
        try:
            data = RelayClient(config).ping()
            print(f"remote : UP protocol={data.get('protocol')}")
        except Exception as exc:
            print(f"remote : DOWN {exc}")
            return 1
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    config = load_config()
    changed = False
    if args.allow_remote_cloud:
        config["allow_remote_cloud"] = True
        changed = True
    if args.deny_remote_cloud:
        config["allow_remote_cloud"] = False
        changed = True
    if args.lease_seconds is not None:
        if not 30 <= args.lease_seconds <= 3600:
            raise RelayError("lease-secondsは30..3600")
        config["lease_seconds"] = args.lease_seconds
        changed = True
    if args.max_execution_seconds is not None:
        if not 30 <= args.max_execution_seconds <= 21600:
            raise RelayError("max-execution-secondsは30..21600")
        config["max_execution_seconds"] = args.max_execution_seconds
        changed = True
    if args.interval is not None:
        if not 0.5 <= args.interval <= 300:
            raise RelayError("intervalは0.5..300")
        config["interval"] = args.interval
        changed = True
    if changed:
        save_config(config)
    masked = dict(config)
    token = str(masked.pop("token", ""))
    masked["token"] = "***" + token[-4:] if token else "missing"
    print(json.dumps(masked, ensure_ascii=False, indent=2))
    return 0


def cmd_unpair(args: argparse.Namespace) -> int:
    if CONFIG_PATH.is_file():
        CONFIG_PATH.unlink()
        print(f"✓ local relay credential deleted: {CONFIG_PATH}")
    else:
        print("relay config already absent")
    print("remote workerを無効化する場合は管理側でもrevokeしてください。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kaigi relay", description="ChatGPT/cloud→local kaigi outbound relay")
    sub = p.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="single-use codeで端末をpair")
    pair.add_argument("code", nargs="?", help="省略時は非表示prompt")
    pair.add_argument("--url")
    pair.add_argument("--name")
    pair.set_defaults(func=cmd_pair)

    ping = sub.add_parser("ping", help="relay認証/接続確認")
    ping.set_defaults(func=cmd_ping)
    once = sub.add_parser("once", help="1件だけclaimして処理")
    once.set_defaults(func=cmd_once)
    serve = sub.add_parser("serve", help="foreground polling worker")
    serve.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    serve.set_defaults(func=cmd_serve)
    start = sub.add_parser("start", help="background worker開始")
    start.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    start.set_defaults(func=cmd_start)
    stop = sub.add_parser("stop", help="background worker停止")
    stop.set_defaults(func=cmd_stop)
    status = sub.add_parser("status", help="local daemon + remote relay状態")
    status.set_defaults(func=cmd_status)
    config = sub.add_parser("config", help="local relay policy/settings")
    cloud = config.add_mutually_exclusive_group()
    cloud.add_argument("--allow-remote-cloud", action="store_true")
    cloud.add_argument("--deny-remote-cloud", action="store_true")
    config.add_argument("--lease-seconds", type=int)
    config.add_argument("--max-execution-seconds", type=int)
    config.add_argument("--interval", type=float)
    config.set_defaults(func=cmd_config)
    unpair = sub.add_parser("unpair", help="local worker credential削除")
    unpair.set_defaults(func=cmd_unpair)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except RelayError as exc:
        core.eprint(f"relay error: {exc}")
        return 1
    except core.KaigiError as exc:
        core.eprint(f"kaigi error: {exc}")
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
