#!/usr/bin/env python3
"""kaigi v6 high-level autopilot + proof packet operations."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
from typing import Any

import kaigi_core as core
import kaigi_ops as ops

VERSION = "6.0.0"
PACKET_SCHEMA = "kaigi.decision_packet.v1"
HANDOFF_SCHEMA = "kaigi.handoff.v1"
PACKETS_DIR = core.STATE_DIR / "packets"
HANDOFFS_DIR = core.STATE_DIR / "handoffs"
DEFAULT_MAX_AGENTS = int(os.environ.get("KAIGI_AUTO_MAX_AGENTS", "4"))
DEFAULT_MIN_AGENTS = int(os.environ.get("KAIGI_AUTO_MIN_AGENTS", "2"))
PREFERRED_AGENTS = [
    "claude", "codex", "chatgpt", "hermes", "gemini", "qwen", "kimi",
    "lmstudio", "localglm", "ollama",
]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _mid(value: Any) -> int:
    if isinstance(value, dict):
        value = core.msg_id(value)
    try:
        return int(value)
    except Exception:
        return -1


def _packet_path(run_id: str) -> pathlib.Path:
    return PACKETS_DIR / f"{run_id}.json"


def _handoff_path(run_id: str) -> pathlib.Path:
    return HANDOFFS_DIR / f"{run_id}.json"


def _atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _normalize_message(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _mid(msg),
        "sender": str(msg.get("sender") or msg.get("author") or "unknown"),
        "text": str(msg.get("text") or msg.get("content") or ""),
        "channel": str(msg.get("channel") or core.DEFAULT_CHANNEL),
    }


def _round_ids(run: dict[str, Any], key: str) -> list[int]:
    out: list[int] = []
    data = run.get(key) or {}
    if isinstance(data, dict):
        for item in data.values():
            if isinstance(item, dict):
                mid = _mid(item.get("id", item.get("message_id", -1)))
                if mid >= 0:
                    out.append(mid)
    return out


def evidence_ids(run: dict[str, Any]) -> list[int]:
    ids = [
        _mid(run.get("kickoff_message_id")),
        *_round_ids(run, "round1"),
        _mid(run.get("round2_message_id")),
        *_round_ids(run, "round2"),
        _mid(run.get("final_request_message_id")),
        _mid(run.get("final_message_id")),
    ]
    return sorted(set(x for x in ids if x >= 0))


def _fetch_evidence(run: dict[str, Any], ids: list[int] | None = None) -> list[dict[str, Any]]:
    ids = evidence_ids(run) if ids is None else sorted(set(ids))
    if not ids:
        raise core.KaigiError("runにevidence message idがありません。")
    channel = str(run.get("channel") or core.DEFAULT_CHANNEL)
    messages = core.fetch_messages(since_id=min(ids) - 1, channel=channel)
    by_id = {_mid(msg): msg for msg in messages}
    missing = [mid for mid in ids if mid not in by_id]
    if missing:
        raise core.KaigiError("会議証拠メッセージが取得できません: " + ", ".join(map(str, missing)))
    return [_normalize_message(by_id[mid]) for mid in ids]


def build_packet(run: dict[str, Any]) -> dict[str, Any]:
    if run.get("kind") != "council":
        raise core.KaigiError("Decision packetはCouncil runのみ対応です。")
    if run.get("state") != "complete" or not run.get("final_text"):
        raise core.KaigiError("会議がcompleteではありません。kaigi resume/reconcileで完了させてください。")
    transcript = _fetch_evidence(run)
    participants = [str(x) for x in run.get("participants", [])]
    packet: dict[str, Any] = {
        "schema": PACKET_SCHEMA,
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "run": {
            "run_id": str(run.get("run_id")),
            "kind": str(run.get("kind")),
            "topic": str(run.get("topic") or ""),
            "channel": str(run.get("channel") or core.DEFAULT_CHANNEL),
            "server": str(run.get("server") or core.SERVER_URL),
            "state": str(run.get("state")),
            "started_at": run.get("started_at"),
            "completed_at": run.get("updated_at"),
            "participants": participants,
            "roles": dict(run.get("roles") or {}),
            "synth": str(run.get("synth") or run.get("final_sender") or ""),
        },
        "decision": {
            "sender": str(run.get("final_sender") or ""),
            "message_id": _mid(run.get("final_message_id")),
            "text": str(run.get("final_text") or ""),
        },
        "rounds": {
            "round1": dict(run.get("round1") or {}),
            "round2": dict(run.get("round2") or {}),
        },
        "message_ids": {
            "kickoff": _mid(run.get("kickoff_message_id")),
            "round2_prompt": _mid(run.get("round2_message_id")),
            "final_prompt": _mid(run.get("final_request_message_id")),
            "final_reply": _mid(run.get("final_message_id")),
        },
        "evidence_ids": evidence_ids(run),
        "transcript": transcript,
        "transcript_sha256": _sha(transcript),
        "authority": {
            "classification": "advisory-decision",
            "execution_authorized": False,
            "note": "Meeting output is evidence/advice, not execution authority.",
        },
    }
    packet["packet_sha256"] = _sha(packet)
    path = _packet_path(str(run["run_id"]))
    _atomic_json(path, packet)
    run["decision_packet"] = {
        "schema": PACKET_SCHEMA,
        "path": str(path),
        "sha256": packet["packet_sha256"],
        "transcript_sha256": packet["transcript_sha256"],
    }
    core.save_run(run)
    return packet


def load_packet(run_id: str | None = None) -> tuple[pathlib.Path, dict[str, Any]]:
    run = core.load_run(run_id)
    path = _packet_path(str(run["run_id"]))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise core.KaigiError(f"Decision packetがありません: {path}\n先に kaigi packet {run['run_id']} を実行してください。") from exc
    if not isinstance(data, dict):
        raise core.KaigiError(f"Decision packetが不正です: {path}")
    return path, data


def verify_packet(packet: dict[str, Any], *, live: bool = False) -> list[str]:
    errors: list[str] = []
    if packet.get("schema") != PACKET_SCHEMA:
        errors.append(f"schema mismatch: {packet.get('schema')}")

    expected_packet = str(packet.get("packet_sha256") or "")
    body = dict(packet)
    body.pop("packet_sha256", None)
    actual_packet = _sha(body)
    if not expected_packet or expected_packet != actual_packet:
        errors.append("packet_sha256 mismatch")

    transcript = packet.get("transcript")
    if not isinstance(transcript, list):
        errors.append("transcript missing")
    else:
        expected_transcript = str(packet.get("transcript_sha256") or "")
        if not expected_transcript or expected_transcript != _sha(transcript):
            errors.append("transcript_sha256 mismatch")

    run_info = packet.get("run") if isinstance(packet.get("run"), dict) else {}
    run_id = str(run_info.get("run_id") or "")
    if run_id:
        try:
            run = core.load_run(run_id)
            if str(run.get("final_text") or "") != str((packet.get("decision") or {}).get("text") or ""):
                errors.append("run ledger final_text differs from packet")
            pointer = run.get("decision_packet") if isinstance(run.get("decision_packet"), dict) else {}
            if pointer.get("sha256") and pointer.get("sha256") != expected_packet:
                errors.append("run ledger packet pointer mismatch")
        except Exception as exc:
            errors.append(f"run ledger unavailable: {exc}")

    if live and not errors:
        try:
            ids = [int(x) for x in packet.get("evidence_ids", [])]
            if not ids:
                raise core.KaigiError("packetにevidence_idsがありません。")
            channel = str(run_info.get("channel") or core.DEFAULT_CHANNEL)
            messages = core.fetch_messages(since_id=min(ids) - 1, channel=channel)
            by_id = {_mid(m): m for m in messages}
            missing = [mid for mid in ids if mid not in by_id]
            if missing:
                errors.append("live evidence missing: " + ", ".join(map(str, missing)))
            else:
                live_transcript = [_normalize_message(by_id[mid]) for mid in sorted(ids)]
                if _sha(live_transcript) != str(packet.get("transcript_sha256") or ""):
                    errors.append("live transcript differs from packet")
        except Exception as exc:
            errors.append(f"live verification failed: {exc}")
    return errors


def _is_cloud_api(cfg: dict[str, Any]) -> bool:
    return cfg.get("type") == "api" and not core.is_local_api(cfg)


def _priority(name: str) -> tuple[int, str]:
    lowered = name.lower()
    try:
        return PREFERRED_AGENTS.index(lowered), lowered
    except ValueError:
        return 1000, lowered


def _classification(name: str, cfg: dict[str, Any] | None, online: set[str]) -> str:
    if cfg and cfg.get("type") == "api":
        return "api-local" if core.is_local_api(cfg) else "api-cloud"
    if cfg and cfg.get("command"):
        return "cli"
    if name in online:
        return "online-unclassified"
    return "unclassified"


def choose_auto_agents(*, allow_cloud: bool, max_agents: int, allow_unknown: bool = False) -> tuple[list[str], dict[str, str]]:
    cfgs = core.load_agents_config()
    online = set(core.online_agents(core.fetch_status())) if core.server_alive() else set()
    names = set(cfgs) | online
    reasons: dict[str, str] = {}
    eligible: list[str] = []
    for name in sorted(names, key=_priority):
        if name in {"user", "system", "telegram-bot"}:
            reasons[name] = "excluded-system"
            continue
        cfg = cfgs.get(name)
        cls = _classification(name, cfg, online)
        if cls == "api-cloud" and not allow_cloud:
            reasons[name] = "excluded-cloud"
            continue
        if cls == "unclassified":
            reasons[name] = "excluded-unconfigured"
            continue
        if cls == "online-unclassified" and not allow_unknown:
            reasons[name] = "excluded-unclassified-online"
            continue
        reasons[name] = cls + (":online" if name in online else ":offline")
        eligible.append(name)
    return eligible[:max(1, max_agents)], reasons


def _prepare_auto(selected: list[str], *, wait: float, launch_cli: bool, include_cloud: bool, strict: bool) -> list[str]:
    cfgs = core.load_agents_config()
    before = set(core.online_agents(core.fetch_status()))
    api = [n for n in selected if cfgs.get(n, {}).get("type") == "api" and n not in before]
    cli = [n for n in selected if cfgs.get(n, {}).get("type") != "api" and n not in before]
    if api:
        core.start_api_agents(api, wait=wait, include_cloud=include_cloud, quiet=False)
    if cli and launch_cli:
        results = ops.launch_cli_agents(cli, wait=wait)
        for name in cli:
            print(f"{name:<16} {results.get(name, 'unknown')}")
    current = set(core.online_agents(core.fetch_status()))
    missing = [name for name in selected if name not in current]
    if missing:
        if strict:
            raise core.KaigiError("会議参加準備ができていないagent: " + ", ".join(missing))
        print(f"{core.YELLOW}!{core.RESET} unavailable: " + ", ".join(missing))
    return [name for name in selected if name in current]


def cmd_decide(args: argparse.Namespace) -> int:
    topic = " ".join(args.topic).strip()
    if not topic:
        raise core.KaigiError("議題が空です。")

    explicit = ops.split_names(args.agents)
    if args.dry_run:
        if explicit:
            selected = explicit
            cfgs = core.load_agents_config()
            online = set(core.online_agents(core.fetch_status())) if core.server_alive() else set()
            reasons = {n: _classification(n, cfgs.get(n), online) for n in selected}
        else:
            selected, reasons = choose_auto_agents(
                allow_cloud=args.allow_cloud,
                max_agents=args.max_agents,
                allow_unknown=args.allow_unknown,
            )
        print(f"topic    : {topic}")
        print("mode     : explicit" if explicit else "mode     : safe-auto")
        print("agents   : " + (", ".join(selected) if selected else "none"))
        for name in selected:
            print(f"  {name:<14} {reasons.get(name, 'selected')}")
        if not explicit:
            excluded = [f"{n}={r}" for n, r in reasons.items() if r.startswith("excluded-")]
            if excluded:
                print("excluded : " + ", ".join(excluded))
        print("action   : dry-run (no launch / no message)")
        return 0

    core.ensure_server()
    if explicit:
        selected = _prepare_auto(explicit, wait=args.wait, launch_cli=not args.no_launch,
                                 include_cloud=True, strict=True)
    else:
        selected, reasons = choose_auto_agents(
            allow_cloud=args.allow_cloud,
            max_agents=args.max_agents,
            allow_unknown=args.allow_unknown,
        )
        if not selected:
            excluded = ", ".join(f"{n}={r}" for n, r in reasons.items())
            raise core.KaigiError("safe-autoで選べるagentがいません。" + (f" ({excluded})" if excluded else ""))
        selected = _prepare_auto(selected, wait=args.wait, launch_cli=not args.no_launch,
                                 include_cloud=args.allow_cloud, strict=False)

    if len(selected) < max(1, args.min_agents):
        raise core.KaigiError(
            f"会議可能agentが不足しています: {len(selected)}/{args.min_agents}。"
            "kaigi agents / kaigi launch / --allow-cloud / --min-agents 1 を確認してください。"
        )

    print(f"{core.CYAN}▶{core.RESET} auto council  agents={','.join(selected)}")
    argv = [
        "convene", topic,
        "--agents", ",".join(selected),
        "--channel", args.channel,
        "--round-timeout", str(args.round_timeout),
        "--quorum", str(args.quorum),
    ]
    if args.synth:
        argv += ["--synth", args.synth]
    code = int(core.main(argv) or 0)
    if code == 0 and not args.no_packet:
        run = core.load_run()
        if run.get("state") == "complete" and run.get("kind") == "council":
            packet = build_packet(run)
            print(f"{core.GREEN}✓{core.RESET} proof packet  sha256={packet['packet_sha256']}")
            print(f"{core.DIM}{_packet_path(str(run['run_id']))}{core.RESET}")
    elif code != 0:
        try:
            run = core.load_run()
            print(f"{core.DIM}resume: kaigi recover {run['run_id']}{core.RESET}")
        except Exception:
            pass
    return code


def cmd_packet(args: argparse.Namespace) -> int:
    core.ensure_server()
    run = core.load_run(args.run_id)
    packet = build_packet(run)
    path = _packet_path(str(run["run_id"]))
    if args.stdout:
        print(json.dumps(packet, ensure_ascii=False, indent=2))
    else:
        print(f"packet   : {path}")
        print(f"sha256   : {packet['packet_sha256']}")
        print(f"transcript: {packet['transcript_sha256']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path, packet = load_packet(args.run_id)
    if args.live:
        core.ensure_server()
    errors = verify_packet(packet, live=args.live)
    if errors:
        print(f"{core.RED}FAIL{core.RESET} {path}")
        for error in errors:
            print(f"  - {error}")
        return 1
    mode = "local+live" if args.live else "local"
    print(f"{core.GREEN}PASS{core.RESET} {path}")
    print(f"mode     : {mode}")
    print(f"sha256   : {packet.get('packet_sha256')}")
    return 0


def _make_handoff(packet: dict[str, Any]) -> dict[str, Any]:
    run = packet.get("run") if isinstance(packet.get("run"), dict) else {}
    decision = packet.get("decision") if isinstance(packet.get("decision"), dict) else {}
    handoff: dict[str, Any] = {
        "schema": HANDOFF_SCHEMA,
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "source": {
            "run_id": run.get("run_id"),
            "packet_sha256": packet.get("packet_sha256"),
            "transcript_sha256": packet.get("transcript_sha256"),
        },
        "topic": run.get("topic"),
        "participants": run.get("participants", []),
        "synth": run.get("synth"),
        "decision": decision,
        "authority": {
            "classification": "advisory",
            "execution_authorized": False,
            "requires_separate_authority": True,
        },
    }
    handoff["handoff_sha256"] = _sha(handoff)
    return handoff


def cmd_handoff(args: argparse.Namespace) -> int:
    try:
        _, packet = load_packet(args.run_id)
    except core.KaigiError:
        if args.no_build:
            raise
        core.ensure_server()
        packet = build_packet(core.load_run(args.run_id))
    errors = verify_packet(packet, live=False)
    if errors:
        raise core.KaigiError("Decision packetの検証に失敗: " + "; ".join(errors))
    handoff = _make_handoff(packet)
    run_id = str((packet.get("run") or {}).get("run_id"))
    path = pathlib.Path(args.output).expanduser() if args.output else _handoff_path(run_id)
    _atomic_json(path, handoff)
    if args.stdout:
        print(json.dumps(handoff, ensure_ascii=False, indent=2))
    else:
        print(f"handoff  : {path}")
        print(f"sha256   : {handoff['handoff_sha256']}")
        print("authority: advisory / execution_authorized=false")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    runs = core.list_runs(args.limit)
    if not runs:
        print("保存済みrunなし")
        return 0
    failed = 0
    for run in runs:
        rid = str(run.get("run_id") or "?")
        state = str(run.get("state") or "?")
        packet_path = _packet_path(rid)
        if packet_path.is_file():
            try:
                packet = json.loads(packet_path.read_text(encoding="utf-8"))
                errors = verify_packet(packet, live=False) if isinstance(packet, dict) else ["invalid packet"]
            except Exception as exc:
                errors = [str(exc)]
            proof = "PASS" if not errors else "FAIL"
            if errors:
                failed += 1
        else:
            proof = "-"
        topic = str(run.get("topic") or "").replace("\n", " ")
        if len(topic) > 46:
            topic = topic[:43] + "..."
        print(f"{rid:<24} {state:<9} proof={proof:<4} {topic}")
    return 1 if failed else 0


def cmd_recover(args: argparse.Namespace) -> int:
    core.ensure_server()
    run = ops.scan_council(core.load_run(args.run_id))
    if run.get("state") == "complete":
        print(f"{core.GREEN}✓{core.RESET} reconciled complete  run={run['run_id']}")
        if not _packet_path(str(run["run_id"])).is_file():
            packet = build_packet(run)
            print(f"{core.GREEN}✓{core.RESET} proof packet  sha256={packet['packet_sha256']}")
        return 0
    ns = argparse.Namespace(
        run_id=str(run["run_id"]),
        round_timeout=args.round_timeout,
        quorum=args.quorum,
        synth=args.synth,
    )
    code = int(ops.cmd_resume(ns) or 0)
    if code == 0:
        run = core.load_run(str(run["run_id"]))
        if run.get("state") == "complete" and not _packet_path(str(run["run_id"])).is_file():
            packet = build_packet(run)
            print(f"{core.GREEN}✓{core.RESET} proof packet  sha256={packet['packet_sha256']}")
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kaigi", description="kaigi v6 autopilot + proof packet")
    sub = p.add_subparsers(dest="command")

    decide = sub.add_parser("decide", aliases=["auto"], help="安全な自動選定→準備→Council→proof packet")
    decide.add_argument("topic", nargs="+")
    decide.add_argument("--agents", help="明示指定。指定時はcloud APIも明示同意として準備可能")
    decide.add_argument("--allow-cloud", action="store_true", help="safe-autoでもcloud API agentを候補にする")
    decide.add_argument("--allow-unknown", action="store_true", help="configで分類不能だがonlineなagentも候補にする")
    decide.add_argument("--max-agents", type=int, default=DEFAULT_MAX_AGENTS)
    decide.add_argument("--min-agents", type=int, default=DEFAULT_MIN_AGENTS)
    decide.add_argument("--wait", type=float, default=8.0)
    decide.add_argument("--no-launch", action="store_true", help="offline CLI agentを自動起動しない")
    decide.add_argument("--channel", "-c", default=core.DEFAULT_CHANNEL)
    decide.add_argument("--round-timeout", type=float, default=60.0)
    decide.add_argument("--quorum", type=int, default=0)
    decide.add_argument("--synth")
    decide.add_argument("--no-packet", action="store_true")
    decide.add_argument("--dry-run", action="store_true")
    decide.set_defaults(func=cmd_decide)

    packet = sub.add_parser("packet", help="complete Councilからhash付きDecision packetを生成")
    packet.add_argument("run_id", nargs="?", default=None)
    packet.add_argument("--stdout", action="store_true")
    packet.set_defaults(func=cmd_packet)

    verify = sub.add_parser("verify", help="Decision packetのhash/ledger/live transcriptを検証")
    verify.add_argument("run_id", nargs="?", default=None)
    verify.add_argument("--live", action="store_true")
    verify.set_defaults(func=cmd_verify)

    handoff = sub.add_parser("handoff", help="下流agent/BLACK向けadvisory handoff packet")
    handoff.add_argument("run_id", nargs="?", default=None)
    handoff.add_argument("--output")
    handoff.add_argument("--stdout", action="store_true")
    handoff.add_argument("--no-build", action="store_true")
    handoff.set_defaults(func=cmd_handoff)

    audit = sub.add_parser("audit", help="最近のrunとproof packet整合を監査")
    audit.add_argument("--limit", type=int, default=20)
    audit.set_defaults(func=cmd_audit)

    recover = sub.add_parser("recover", help="reconcile→不足stageだけresume→packet生成")
    recover.add_argument("run_id", nargs="?", default=None)
    recover.add_argument("--round-timeout", type=float, default=60.0)
    recover.add_argument("--quorum", type=int, default=0)
    recover.add_argument("--synth")
    recover.set_defaults(func=cmd_recover)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return int(args.func(args) or 0)
    except core.KaigiError as exc:
        core.eprint(f"{core.RED}エラー:{core.RESET} {exc}")
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
