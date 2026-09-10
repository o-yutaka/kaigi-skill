#!/usr/bin/env python3
"""Optional high-level operations for kaigi v5: CLI launch, go, reconcile, resume."""
from __future__ import annotations

import argparse
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

import kaigi_core as core


def split_names(value: str | None) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for item in value.split(","):
        name = item.strip().lstrip("@")
        if name and name not in out:
            out.append(name)
    return out


def shell_line(agent: str) -> str:
    py = core.python_bin()
    wrapper = core.HOME / "wrapper.py"
    if not py or not wrapper.is_file():
        raise core.KaigiError("agentchattr wrapper.py またはvenvが見つかりません。")
    return f"cd {shlex.quote(str(core.HOME))} && {shlex.quote(str(py))} {shlex.quote(str(wrapper))} {shlex.quote(agent)}"


def terminal_spec(agent: str) -> tuple[str, list[str] | str]:
    line = shell_line(agent)
    if os.environ.get("TMUX") and shutil.which("tmux"):
        return "tmux", ["tmux", "new-window", "-d", "-n", f"kaigi-{agent}", line]
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro and shutil.which("wt.exe") and shutil.which("wsl.exe"):
        return "windows-terminal", [
            "wt.exe", "-w", "0", "new-tab", "--title", f"kaigi:{agent}",
            "wsl.exe", "-d", distro, "bash", "-lc", line + "; exec bash",
        ]
    if shutil.which("gnome-terminal"):
        return "gnome-terminal", ["gnome-terminal", "--", "bash", "-lc", line + "; exec bash"]
    if shutil.which("x-terminal-emulator"):
        return "x-terminal-emulator", ["x-terminal-emulator", "-e", "bash", "-lc", line + "; exec bash"]
    if shutil.which("xterm"):
        return "xterm", ["xterm", "-T", f"kaigi:{agent}", "-e", "bash", "-lc", line + "; exec bash"]
    if sys.platform == "darwin" and shutil.which("osascript"):
        escaped = line.replace("\\", "\\\\").replace('"', '\\"')
        return "terminal.app", ["osascript", "-e", f'tell app "Terminal" to do script "{escaped}"']
    return "none", line


def launch_cli_agents(names: list[str], *, wait: float = 8.0, dry_run: bool = False,
                      here: bool = False) -> dict[str, str]:
    core.ensure_server()
    cfgs = core.load_agents_config()
    online = set(core.online_agents(core.fetch_status()))
    results: dict[str, str] = {}
    if here and len(names) != 1:
        raise core.KaigiError("--here は1 agentだけ指定してください。")
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
        if not shutil.which(command):
            results[name] = f"command-not-found:{command}"
            continue
        if here:
            line = shell_line(name)
            if dry_run:
                results[name] = f"dry-run:here:{line}"
                continue
            results[name] = "attached"
            subprocess.call(["bash", "-lc", line])
            continue
        mode, spec = terminal_spec(name)
        if mode == "none":
            results[name] = "terminal-not-found"
            continue
        if dry_run:
            shown = spec if isinstance(spec, str) else " ".join(shlex.quote(x) for x in spec)
            results[name] = f"dry-run:{mode}:{shown}"
            continue
        assert isinstance(spec, list)
        try:
            subprocess.Popen(spec, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            results[name] = f"launched:{mode}"
        except OSError as exc:
            results[name] = f"launch-failed:{exc}"

    if not dry_run and not here:
        pending = {n for n, v in results.items() if v.startswith("launched:")}
        deadline = time.time() + max(0.0, wait)
        while pending and time.time() < deadline:
            current = set(core.online_agents(core.fetch_status()))
            for name in list(pending):
                if name in current:
                    results[name] = "online"
                    pending.remove(name)
            if pending:
                time.sleep(0.35)
    return results


def cmd_launch(args: argparse.Namespace) -> int:
    cfgs = core.load_agents_config()
    names = list(args.names)
    if args.all:
        names = [n for n, cfg in cfgs.items() if cfg.get("type") != "api" and cfg.get("command")]
    if not names:
        raise core.KaigiError("起動するCLI agentを指定してください。例: kaigi launch claude codex")
    results = launch_cli_agents(names, wait=args.wait, dry_run=args.dry_run, here=args.here)
    bad = False
    for name in names:
        value = results.get(name, "unknown")
        print(f"{name:<16} {value}")
        if not (value == "online" or value == "attached" or value.startswith("dry-run:")):
            bad = True
    return 1 if bad else 0


def _mid(msg: dict[str, Any]) -> int:
    try:
        return int(core.msg_id(msg))
    except Exception:
        return -1


def _reply_map(messages: list[dict[str, Any]], targets: list[str], after: int,
               before: int | None = None) -> dict[str, dict[str, Any]]:
    target_set = set(targets)
    replies: dict[str, dict[str, Any]] = {}
    for msg in messages:
        mid = _mid(msg)
        if mid <= after or (before is not None and mid >= before):
            continue
        sender = str(msg.get("sender") or "")
        if sender in target_set and sender not in replies:
            replies[sender] = msg
    return replies


def scan_council(run: dict[str, Any]) -> dict[str, Any]:
    if run.get("kind") != "council":
        raise core.KaigiError("reconcile/resume は現在Council runのみ対応です。")
    kickoff = int(run.get("kickoff_message_id") or -1)
    if kickoff < 0:
        raise core.KaigiError("runにkickoff_message_idがありません。")
    channel = str(run.get("channel") or core.DEFAULT_CHANNEL)
    messages = core.fetch_messages(since_id=kickoff, channel=channel)
    rid = str(run.get("run_id"))
    token = f"RUN={rid}"
    round2_id = int(run.get("round2_message_id") or -1)
    final_id = int(run.get("final_request_message_id") or -1)

    for msg in messages:
        if str(msg.get("sender") or "") != "user":
            continue
        text = str(msg.get("text") or "")
        if token not in text:
            continue
        if "KAIGI COUNCIL / ROUND 2" in text and round2_id < 0:
            round2_id = _mid(msg)
            run["round2_message_id"] = round2_id
        elif "KAIGI COUNCIL / FINAL" in text and final_id < 0:
            final_id = _mid(msg)
            run["final_request_message_id"] = final_id

    participants = [str(x) for x in run.get("participants", [])]
    r1_before = round2_id if round2_id >= 0 else (final_id if final_id >= 0 else None)
    round1 = _reply_map(messages, participants, kickoff, r1_before)
    if round1:
        run["round1"] = {a: {"id": _mid(m), "text": str(m.get("text", ""))} for a, m in round1.items()}
        run["responders"] = [a for a in participants if a in round1]

    responders = [str(x) for x in run.get("responders", [])] or list((run.get("round1") or {}).keys())
    if round2_id >= 0 and responders:
        round2 = _reply_map(messages, responders, round2_id, final_id if final_id >= 0 else None)
        if round2:
            run["round2"] = {a: {"id": _mid(m), "text": str(m.get("text", ""))} for a, m in round2.items()}

    synth = str(run.get("synth") or "")
    if final_id >= 0 and synth:
        finals = _reply_map(messages, [synth], final_id, None)
        if synth in finals:
            msg = finals[synth]
            run.update(
                state="complete", stage="complete", final_sender=synth,
                final_message_id=_mid(msg), final_text=str(msg.get("text", "")),
            )
    core.save_run(run)
    return run


def cmd_reconcile(args: argparse.Namespace) -> int:
    core.ensure_server()
    run = scan_council(core.load_run(args.run_id))
    print(f"run      : {run.get('run_id')}")
    print(f"state    : {run.get('state')} / {run.get('stage')}")
    if run.get("final_text"):
        print(f"\nFINAL\n{run['final_text']}")
    else:
        r1 = len(run.get("round1") or {})
        r2 = len(run.get("round2") or {})
        print(f"observed : round1={r1} round2={r2} final=0")
    return 0


def _merge_saved(run: dict[str, Any], key: str, replies: dict[str, dict[str, Any]]) -> None:
    saved = dict(run.get(key) or {})
    for name, msg in replies.items():
        saved[name] = {"id": _mid(msg), "text": str(msg.get("text", ""))}
    run[key] = saved


def _wait_and_save(run: dict[str, Any], key: str, targets: list[str], after: int,
                   quorum: int, timeout: float) -> dict[str, Any]:
    existing = dict(run.get(key) or {})
    if len(existing) >= quorum:
        return existing
    replies, _ = core.wait_agent_round(targets, after, str(run.get("channel") or core.DEFAULT_CHANNEL), timeout, quorum)
    _merge_saved(run, key, replies)
    core.save_run(run)
    return dict(run.get(key) or {})


def cmd_resume(args: argparse.Namespace) -> int:
    core.ensure_server()
    run = scan_council(core.load_run(args.run_id))
    if run.get("state") == "complete" and run.get("final_text"):
        print(f"{core.GREEN}✓{core.RESET} already complete  run={run['run_id']}")
        print(run["final_text"])
        return 0

    participants = [str(x) for x in run.get("participants", [])]
    if not participants:
        raise core.KaigiError("runにparticipantsがありません。")
    channel = str(run.get("channel") or core.DEFAULT_CHANNEL)
    topic = str(run.get("topic") or "")
    quorum = args.quorum if args.quorum > 0 else len(participants)
    quorum = max(1, min(quorum, len(participants)))
    kickoff = int(run.get("kickoff_message_id") or -1)

    if not run.get("round2_message_id"):
        round1 = _wait_and_save(run, "round1", participants, kickoff, quorum, args.round_timeout)
        if len(round1) < quorum:
            run.update(state="waiting", stage="round1_timeout")
            core.save_run(run)
            print(f"{core.YELLOW}!{core.RESET} ROUND1 pending {len(round1)}/{quorum}  run={run['run_id']}")
            return 2
        responders = [a for a in participants if a in round1]
        run["responders"] = responders
        mentions = " ".join(f"@{a}" for a in responders)
        text = (
            f"{mentions} [KAIGI COUNCIL / ROUND 2] RUN={run['run_id']} 議題: {topic}\n"
            "直前の独立案を相互批判する。最も危険な前提/見落としを指摘し、自分の案を必要なら修正する。"
            "賛成だけで終わらせず残る異論を明示する。このラウンドでは他エージェントを@mentionしない。"
        )
        msg = core.send_message(text, channel)
        run.update(state="running", stage="round2", round2_message_id=_mid(msg) if isinstance(msg, dict) else -1)
        core.save_run(run)

    run = scan_council(run)
    responders = [str(x) for x in run.get("responders", [])] or list((run.get("round1") or {}).keys())
    if not responders:
        raise core.KaigiError("ROUND1 responderがありません。")
    q2 = args.quorum if args.quorum > 0 else len(responders)
    q2 = max(1, min(q2, len(responders)))
    round2_id = int(run.get("round2_message_id") or -1)

    if not run.get("final_request_message_id"):
        round2 = _wait_and_save(run, "round2", responders, round2_id, q2, args.round_timeout)
        if len(round2) < q2:
            run.update(state="waiting", stage="round2_timeout")
            core.save_run(run)
            print(f"{core.YELLOW}!{core.RESET} ROUND2 pending {len(round2)}/{q2}  run={run['run_id']}")
            return 2
        synth = args.synth.lstrip("@") if args.synth else str(run.get("synth") or "")
        if not synth or synth not in responders:
            synth = core.choose_synth(responders, args.synth)
        run["synth"] = synth
        text = (
            f"@{synth} [KAIGI COUNCIL / FINAL] RUN={run['run_id']} 議題: {topic}\n"
            f"ROUND1応答: {', '.join(responders)}。ROUND2応答: {', '.join(a for a in responders if a in round2) or 'なし'}。\n"
            "全発言を比較し、単なる要約ではなく最終判断を出す。"
            "DECISION / WHY / DISSENT / RISKS / NEXT ACTIONS の順で、確定と未確定を分離する。"
        )
        msg = core.send_message(text, channel)
        run.update(state="running", stage="final", final_request_message_id=_mid(msg) if isinstance(msg, dict) else -1)
        core.save_run(run)

    run = scan_council(run)
    if run.get("state") == "complete":
        print(f"{core.GREEN}✓{core.RESET} council完了  run={run['run_id']}")
        print(run.get("final_text", ""))
        return 0
    synth = str(run.get("synth") or "")
    final_id = int(run.get("final_request_message_id") or -1)
    final, _ = core.wait_agent_round([synth], final_id, channel, args.round_timeout, 1)
    if synth in final:
        msg = final[synth]
        run.update(state="complete", stage="complete", final_sender=synth,
                   final_message_id=_mid(msg), final_text=str(msg.get("text", "")))
        core.save_run(run)
        print(f"{core.GREEN}✓{core.RESET} council完了  run={run['run_id']}")
        print(run["final_text"])
        return 0
    run.update(state="waiting", stage="final_timeout")
    core.save_run(run)
    print(f"{core.YELLOW}!{core.RESET} FINAL pending  run={run['run_id']}")
    return 2


def cmd_go(args: argparse.Namespace) -> int:
    core.ensure_server()
    names = split_names(args.agents)
    if names:
        cfgs = core.load_agents_config()
        api_names = [n for n in names if cfgs.get(n, {}).get("type") == "api"]
        cli_names = [n for n in names if n not in api_names]
        if api_names:
            core.start_api_agents(api_names, wait=args.wait, include_cloud=True, quiet=False)
        if cli_names:
            results = launch_cli_agents(cli_names, wait=args.wait)
            for name in cli_names:
                print(f"{name:<16} {results.get(name, 'unknown')}")
        current = set(core.online_agents(core.fetch_status()))
        missing = [n for n in names if n not in current]
        if missing:
            raise core.KaigiError("会議参加準備ができていないagent: " + ", ".join(missing))
    argv = ["convene", *args.topic]
    if names:
        argv += ["--agents", ",".join(names)]
    if args.quorum:
        argv += ["--quorum", str(args.quorum)]
    if args.synth:
        argv += ["--synth", args.synth]
    argv += ["--round-timeout", str(args.round_timeout)]
    return core.main(argv)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kaigi", description="kaigi v5 high-level operations")
    sub = p.add_subparsers(dest="command", required=True)

    launch = sub.add_parser("launch", help="CLI agentを公式wrapper.pyで新terminalへ起動")
    launch.add_argument("names", nargs="*")
    launch.add_argument("--all", action="store_true", help="設定済みCLI agent全部")
    launch.add_argument("--wait", type=float, default=8.0)
    launch.add_argument("--dry-run", action="store_true")
    launch.add_argument("--here", action="store_true", help="新terminalを使わず現在terminalで1 agent起動")
    launch.set_defaults(func=cmd_launch)

    go = sub.add_parser("go", help="指定agentを起こしてCouncilを開始")
    go.add_argument("topic", nargs="+")
    go.add_argument("--agents", help="claude,codex,chatgpt")
    go.add_argument("--wait", type=float, default=8.0)
    go.add_argument("--quorum", type=int, default=0)
    go.add_argument("--synth")
    go.add_argument("--round-timeout", type=float, default=60.0)
    go.set_defaults(func=cmd_go)

    rec = sub.add_parser("reconcile", aliases=["sync"], help="chat実績から未完了runを副作用なしで再照合")
    rec.add_argument("run_id", nargs="?", default="latest")
    rec.set_defaults(func=cmd_reconcile)

    resume = sub.add_parser("resume", help="未完了Councilを保存stageから再開")
    resume.add_argument("run_id", nargs="?", default="latest")
    resume.add_argument("--round-timeout", type=float, default=60.0)
    resume.add_argument("--quorum", type=int, default=0)
    resume.add_argument("--synth")
    resume.set_defaults(func=cmd_resume)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
