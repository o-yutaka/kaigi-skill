#!/usr/bin/env python3
"""kaigi v7 — invocation-bound proof, runtime provenance, and upstream Jobs queue bridge."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Any, Callable

import kaigi_core as core
import kaigi_v6 as v6

VERSION = "7.0.0"
RUNTIME_FILES = ("kaigi", "kaigi_core.py", "kaigi_ops.py", "kaigi_v6.py", "kaigi_v7.py")
JOB_RUN_MARKER = "KAIGI-RUN:"
JOB_PACKET_MARKER = "KAIGI-PACKET:"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def runtime_provenance() -> dict[str, Any]:
    root = pathlib.Path(__file__).resolve().parent
    files: dict[str, dict[str, Any]] = {}
    for name in RUNTIME_FILES:
        path = root / name
        if not path.is_file():
            files[name] = {"missing": True}
            continue
        raw = path.read_bytes()
        files[name] = {"sha256": _sha_bytes(raw), "size": len(raw)}
    body: dict[str, Any] = {
        "kaigi_version": VERSION,
        "files": files,
    }
    body["runtime_sha256"] = v6._sha(body)
    return body


def parse_decision_sections(text: str) -> dict[str, str]:
    aliases = {
        "DECISION": "decision",
        "WHY": "why",
        "DISSENT": "dissent",
        "RISKS": "risks",
        "NEXT ACTIONS": "next_actions",
        "NEXT ACTION": "next_actions",
    }
    pattern = re.compile(r"^\s*(DECISION|WHY|DISSENT|RISKS|NEXT ACTIONS?|NEXT_ACTIONS?)\s*[:：-]?\s*(.*)$", re.I)
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in str(text or "").splitlines():
        match = pattern.match(raw)
        if match:
            heading = match.group(1).upper().replace("_", " ")
            key = aliases.get(heading, heading.lower().replace(" ", "_"))
            current = key
            sections.setdefault(key, [])
            if match.group(2).strip():
                sections[key].append(match.group(2).strip())
            continue
        if current is not None:
            sections[current].append(raw.rstrip())
    return {key: "\n".join(lines).strip() for key, lines in sections.items() if "\n".join(lines).strip()}


def _enhance_packet(packet: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    decision = packet.get("decision") if isinstance(packet.get("decision"), dict) else {}
    decision = dict(decision)
    decision["sections"] = parse_decision_sections(str(decision.get("text") or run.get("final_text") or ""))
    packet["decision"] = decision
    packet["provenance"] = runtime_provenance()
    body = dict(packet)
    body.pop("packet_sha256", None)
    packet["packet_sha256"] = v6._sha(body)
    path = v6._packet_path(str(run["run_id"]))
    v6._atomic_json(path, packet)
    run["decision_packet"] = {
        "schema": packet.get("schema", v6.PACKET_SCHEMA),
        "path": str(path),
        "sha256": packet["packet_sha256"],
        "transcript_sha256": packet.get("transcript_sha256"),
        "runtime_sha256": packet["provenance"]["runtime_sha256"],
    }
    core.save_run(run)
    return packet


def enhanced_build_packet(run: dict[str, Any], original: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    builder = original or v6.build_packet
    # Guard against accidentally passing this function after monkey-patching.
    if builder is enhanced_build_packet:
        raise core.KaigiError("packet builder recursion")
    return _enhance_packet(builder(run), run)


def verify_packet_v7(packet: dict[str, Any], *, live: bool = False, current_runtime: bool = False) -> list[str]:
    errors = list(v6.verify_packet(packet, live=live))
    prov = packet.get("provenance")
    if isinstance(prov, dict):
        body = dict(prov)
        expected = str(body.pop("runtime_sha256", ""))
        if not expected or expected != v6._sha(body):
            errors.append("runtime provenance hash mismatch")
        if current_runtime and expected != runtime_provenance().get("runtime_sha256"):
            errors.append("current runtime differs from packet provenance")
    elif current_runtime:
        errors.append("packet has no runtime provenance")
    return errors


def _with_enhanced_builder(fn: Callable[[], int]) -> int:
    original = v6.build_packet

    def builder(run: dict[str, Any]) -> dict[str, Any]:
        return enhanced_build_packet(run, original)

    v6.build_packet = builder
    try:
        return int(fn() or 0)
    finally:
        v6.build_packet = original


def run_v6(argv: list[str], *, bind_invocation: bool = False) -> int:
    """Run v6 surface while ensuring v7 packet enrichment and optional exact run binding."""
    if not bind_invocation:
        return _with_enhanced_builder(lambda: v6.main(argv))

    captured: dict[str, str] = {}
    original_new_run = core.new_run
    original_load_run = core.load_run
    original_build = v6.build_packet

    def new_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        run = original_new_run(*args, **kwargs)
        captured["run_id"] = str(run["run_id"])
        return run

    def load_run(run_id: str | None = None) -> dict[str, Any]:
        if run_id is None and captured.get("run_id"):
            return original_load_run(captured["run_id"])
        return original_load_run(run_id)

    def builder(run: dict[str, Any]) -> dict[str, Any]:
        return enhanced_build_packet(run, original_build)

    core.new_run = new_run
    core.load_run = load_run
    v6.build_packet = builder
    try:
        return int(v6.main(argv) or 0)
    finally:
        core.new_run = original_new_run
        core.load_run = original_load_run
        v6.build_packet = original_build


def _load_or_build_packet(run_id: str | None, *, build: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    run = core.load_run(run_id)
    try:
        _, packet = v6.load_packet(str(run["run_id"]))
    except core.KaigiError:
        if not build:
            raise
        core.ensure_server()
        packet = enhanced_build_packet(run)
    if not isinstance(packet.get("provenance"), dict) and build:
        core.ensure_server()
        packet = enhanced_build_packet(run)
    return run, packet


def _job_marker(run_id: str, packet_sha: str) -> str:
    return f"{JOB_RUN_MARKER}{run_id}\n{JOB_PACKET_MARKER}{packet_sha}"


def _job_body(packet: dict[str, Any]) -> str:
    run = packet.get("run") if isinstance(packet.get("run"), dict) else {}
    decision = packet.get("decision") if isinstance(packet.get("decision"), dict) else {}
    marker = _job_marker(str(run.get("run_id") or ""), str(packet.get("packet_sha256") or ""))
    authority = "AUTHORITY: advisory; execution_authorized=false; requires_separate_authority=true"
    text = str(decision.get("text") or "").strip()
    prefix = f"{marker}\n{authority}\n\nDECISION:\n"
    budget = max(0, 990 - len(prefix))
    if len(text) > budget:
        text = text[: max(0, budget - 3)] + "..."
    return prefix + text


def _jobs_list(channel: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    params = {"channel": channel, "status": status}
    data = core.request_json("GET", "/api/jobs", params=params)
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _find_queued_job(jobs: list[dict[str, Any]], run_id: str, packet_sha: str) -> dict[str, Any] | None:
    marker = _job_marker(run_id, packet_sha)
    for job in jobs:
        if marker in str(job.get("body") or ""):
            return job
    return None


def _rollback_job(job_id: int) -> str:
    try:
        core.request_json("DELETE", f"/api/jobs/{job_id}", params={"permanent": "true"})
        return "rolled-back"
    except Exception as exc:
        return f"rollback-failed:{exc}"


def cmd_queue(args: argparse.Namespace) -> int:
    if args.dry_run:
        run, packet = _load_or_build_packet(args.run_id, build=False)
        errors = verify_packet_v7(packet, live=False, current_runtime=False)
        if errors:
            raise core.KaigiError("Decision packet verification failed: " + "; ".join(errors))
        print(f"run      : {run['run_id']}")
        print(f"job title: {args.title or '[kaigi] ' + str(run.get('topic') or '')}")
        print(f"assignee : {args.assignee or '-'}")
        print("status   : open / TO DO")
        print("authority: advisory; execution_authorized=false")
        print("action   : dry-run (no Jobs API write)")
        return 0

    core.ensure_server()
    run, packet = _load_or_build_packet(args.run_id, build=True)
    errors = verify_packet_v7(packet, live=not args.no_live, current_runtime=False)
    if errors:
        raise core.KaigiError("Decision packet verification failed: " + "; ".join(errors))

    run_id = str(run["run_id"])
    packet_sha = str(packet.get("packet_sha256") or "")
    existing = _find_queued_job(_jobs_list(channel=str(run.get("channel") or core.DEFAULT_CHANNEL)), run_id, packet_sha)
    if existing:
        print(f"{core.GREEN}✓{core.RESET} already queued  job={existing.get('id')} status={existing.get('status')}")
        return 0

    title = (args.title or f"[kaigi] {str(run.get('topic') or '').strip() or run_id}")[:120]
    payload = {
        "title": title,
        "type": "job",
        "channel": str(run.get("channel") or core.DEFAULT_CHANNEL),
        "created_by": "kaigi",
        "anchor_msg_id": int((packet.get("decision") or {}).get("message_id") or 0) or None,
        "assignee": args.assignee or "",
        "body": _job_body(packet),
    }
    created = core.request_json("POST", "/api/jobs", payload=payload)
    if not isinstance(created, dict) or not created.get("id"):
        raise core.KaigiError("Jobs API create responseが不正です。")
    job_id = int(created["id"])

    # Upstream JobStore.create defaults to internal `done` (= ACTIVE in UI).
    # Immediately move to `open` (= TO DO). If that fails, permanently roll back
    # the just-created job so kaigi never leaves an accidental ACTIVE task behind.
    try:
        updated = core.request_json("PATCH", f"/api/jobs/{job_id}", payload={"status": "open"})
    except Exception as exc:
        rollback = _rollback_job(job_id)
        raise core.KaigiError(f"jobをTO DOへ移せませんでした ({exc}); {rollback}") from exc
    if not isinstance(updated, dict) or updated.get("status") != "open":
        rollback = _rollback_job(job_id)
        raise core.KaigiError(f"job statusがopenになりませんでした; {rollback}")

    run["job_bridge"] = {
        "job_id": job_id,
        "job_uid": updated.get("uid"),
        "status": "open",
        "packet_sha256": packet_sha,
        "transcript_sha256": packet.get("transcript_sha256"),
        "authority": "advisory",
        "execution_authorized": False,
    }
    core.save_run(run)
    print(f"{core.GREEN}✓{core.RESET} queued  job={job_id} status=open/TO DO")
    print(f"packet   : {packet_sha}")
    print("authority: advisory / execution_authorized=false")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    core.ensure_server()
    jobs = _jobs_list(channel=args.channel, status=args.status)
    if not jobs:
        print("jobsなし")
        return 0
    for job in jobs:
        body = str(job.get("body") or "")
        origin = "kaigi" if JOB_RUN_MARKER in body else "-"
        title = str(job.get("title") or "").replace("\n", " ")
        print(f"{str(job.get('id','?')):>4} {str(job.get('status','?')):<9} {str(job.get('assignee') or '-'):<12} source={origin:<5} {title}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    _, packet = _load_or_build_packet(args.run_id, build=False)
    if args.live:
        core.ensure_server()
    errors = verify_packet_v7(packet, live=args.live, current_runtime=args.current_runtime)
    path = v6._packet_path(str((packet.get("run") or {}).get("run_id") or "unknown"))
    if errors:
        print(f"{core.RED}FAIL{core.RESET} {path}")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"{core.GREEN}PASS{core.RESET} {path}")
    print(f"mode     : {'local+live' if args.live else 'local'}")
    prov = packet.get("provenance") if isinstance(packet.get("provenance"), dict) else {}
    print(f"runtime  : {prov.get('runtime_sha256', 'legacy/none')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kaigi", description="kaigi v7 queue/provenance operations")
    sub = p.add_subparsers(dest="command")

    q = sub.add_parser("queue", aliases=["todo"], help="verified Decisionをagentchattr JobsのTO DOへ安全に登録")
    q.add_argument("run_id", nargs="?", default=None)
    q.add_argument("--title")
    q.add_argument("--assignee")
    q.add_argument("--no-live", action="store_true", help="live transcript再照合を省略")
    q.add_argument("--dry-run", action="store_true", help="Jobs APIへ書かない。既存packet必須")
    q.set_defaults(func=cmd_queue)

    j = sub.add_parser("jobs", help="upstream agentchattr Jobsを表示")
    j.add_argument("--channel")
    j.add_argument("--status", choices=["open", "done", "archived"])
    j.set_defaults(func=cmd_jobs)

    v = sub.add_parser("verify", help="v7 packet/ledger/live/runtime provenance検証")
    v.add_argument("run_id", nargs="?", default=None)
    v.add_argument("--live", action="store_true")
    v.add_argument("--current-runtime", action="store_true", help="現在のkaigi runtime fingerprintとも一致要求")
    v.set_defaults(func=cmd_verify)
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
