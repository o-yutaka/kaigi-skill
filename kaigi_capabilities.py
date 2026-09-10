#!/usr/bin/env python3
"""Capability-aware cast selection for kaigi v7.

Provider names are identities only. Selection is driven by declared capabilities,
runtime facts, cost metadata and observed response reliability.
"""
from __future__ import annotations

import argparse
import contextvars
import datetime as dt
import hashlib
import itertools
import json
import os
import pathlib
import re
from typing import Any

REGISTRY_SCHEMA = "kaigi.capability_registry.v1"
PLAN_SCHEMA = "kaigi.capability_plan.v1"
SELECTION_POLICY = "capability-cost-reliability-v1"
CONFIG_DIR = pathlib.Path(os.environ.get("KAIGI_CONFIG_DIR", str(pathlib.Path.home() / ".config/kaigi"))).expanduser()
REGISTRY_PATH = pathlib.Path(os.environ.get("KAIGI_CAPABILITY_REGISTRY", str(CONFIG_DIR / "capabilities.json"))).expanduser()

KNOWN_CAPABILITIES = [
    "general", "coding", "research", "red-team", "vision", "long-context",
    "local-free", "fast", "deep-reasoning",
]
COST_RANK = {"free": 0, "local": 0, "low": 1, "unknown": 2, "metered": 3}
SPEED_RANK = {"fast": 0, "normal": 1, "unknown": 2, "deep": 3}
ROLE_CAPABILITIES = {
    "planner": {"deep-reasoning", "research", "long-context"},
    "red-team": {"red-team"},
    "implementer": {"coding"},
    "evidence": {"research", "long-context"},
    "ux": {"vision"},
    "long-horizon": {"long-context", "deep-reasoning"},
}
SYSTEM_AGENTS = {"user", "system", "telegram-bot"}
_ACTIVE_PLAN: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "kaigi_capability_plan", default=None
)


class CapabilityError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normalize_cap(value: str) -> str:
    cap = value.strip().lower().replace("_", "-").replace(" ", "-")
    cap = re.sub(r"-+", "-", cap).strip("-")
    if not cap or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", cap):
        raise CapabilityError(f"capability名が不正です: {value!r}")
    return cap


def parse_caps(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        for item in str(value).split(","):
            if item.strip():
                cap = _normalize_cap(item)
                if cap not in out:
                    out.append(cap)
    return out


def _empty_registry() -> dict[str, Any]:
    return {"schema": REGISTRY_SCHEMA, "agents": {}}


def load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.is_file():
        return _empty_registry()
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CapabilityError(f"capability registryを読めません: {REGISTRY_PATH}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != REGISTRY_SCHEMA or not isinstance(data.get("agents"), dict):
        raise CapabilityError(f"capability registry schemaが不正です: {REGISTRY_PATH}")
    return data


def save_registry(data: dict[str, Any]) -> None:
    data = dict(data)
    data["schema"] = REGISTRY_SCHEMA
    data.setdefault("agents", {})
    data["updated_at"] = dt.datetime.now().astimezone().isoformat()
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def registry_hash(registry: dict[str, Any] | None = None) -> str:
    return _sha(registry if registry is not None else load_registry())


def _contains(text: str, needle: str) -> bool:
    if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", needle):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text))
    return needle in text


def infer_topic(topic: str) -> list[str]:
    text = topic.lower()
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("coding", ("code", "coding", "implement", "implementation", "refactor", "bug", "test", "pull request", "repository", "repo", "pr", "コード", "実装", "修正", "バグ", "テスト", "リポジトリ")),
        ("research", ("research", "investigate", "compare", "latest", "official", "source", "調査", "比較", "最新", "公式", "一次情報", "探して", "調べ")),
        ("red-team", ("red-team", "security", "threat", "attack", "risk", "abuse", "セキュリティ", "脅威", "攻撃", "リスク", "安全性", "反証", "悪用")),
        ("vision", ("vision", "image", "screenshot", "photo", "ui", "ux", "design", "画像", "スクショ", "写真", "画面", "デザイン")),
        ("long-context", ("architecture", "codebase", "repo-wide", "whole repo", "long context", "全体", "横断", "大規模", "アーキテクチャ", "コードベース", "長文")),
        ("deep-reasoning", ("prove", "proof", "causal", "reasoning", "decision", "tradeoff", "architecture", "証明", "因果", "設計", "判断", "トレードオフ", "複雑")),
        ("fast", ("fast", "quick", "urgent", "すぐ", "高速", "急ぎ")),
    ]
    return [cap for cap, words in rules if any(_contains(text, word) for word in words)]


def _config_caps(cfg: dict[str, Any] | None) -> list[str]:
    if not cfg:
        return []
    raw = cfg.get("capabilities")
    try:
        return parse_caps(raw)
    except CapabilityError:
        return []


def _history_stats(core: Any, name: str, limit: int = 100) -> dict[str, Any]:
    invited = r1 = r2 = finals = 0
    try:
        runs = core.list_runs(limit=limit)
    except Exception:
        runs = []
    for run in runs:
        if not isinstance(run, dict) or run.get("kind") != "council":
            continue
        participants = [str(x) for x in run.get("participants", [])]
        if name not in participants:
            continue
        invited += 1
        if name in (run.get("round1") or {}):
            r1 += 1
        if name in (run.get("round2") or {}):
            r2 += 1
        if run.get("state") == "complete" and str(run.get("final_sender") or "") == name:
            finals += 1
    rate = round(r1 / invited, 4) if invited else None
    return {"invited": invited, "round1_replies": r1, "round2_replies": r2, "successful_finals": finals, "round1_response_rate": rate}


def merged_profile(v6: Any, name: str, cfg: dict[str, Any] | None, online: set[str], registry: dict[str, Any]) -> dict[str, Any]:
    cls = v6._classification(name, cfg, online)
    entry = (registry.get("agents") or {}).get(name, {})
    if not isinstance(entry, dict):
        entry = {}
    caps = {"general"}
    caps.update(_config_caps(cfg))
    if cls == "api-local":
        caps.add("local-free")
    if cls == "cli":
        caps.add("cli")
    caps.update(parse_caps(entry.get("capabilities", [])))
    cost = str(entry.get("cost") or (cfg or {}).get("cost") or ("local" if cls == "api-local" else "unknown")).lower()
    if cost not in COST_RANK:
        cost = "unknown"
    speed = str(entry.get("speed") or (cfg or {}).get("speed") or "unknown").lower()
    if speed not in SPEED_RANK:
        speed = "unknown"
    context_tokens = entry.get("context_tokens", (cfg or {}).get("context_tokens"))
    try:
        context_tokens = int(context_tokens) if context_tokens is not None else None
    except (TypeError, ValueError):
        context_tokens = None
    return {
        "name": name,
        "classification": cls,
        "online": name in online,
        "enabled": bool(entry.get("enabled", True)),
        "capabilities": sorted(caps),
        "cost": cost,
        "speed": speed,
        "context_tokens": context_tokens,
        "reliability": _history_stats(v6.core, name),
        "source": "registry+runtime" if entry else "runtime/config",
    }


def _role_affinity(profile: dict[str, Any], role: str) -> int:
    caps = set(profile.get("capabilities", []))
    return len(caps & ROLE_CAPABILITIES.get(role, set()))


def _arrange_for_roles(core: Any, selected: list[str], profiles: dict[str, dict[str, Any]]) -> list[str]:
    if len(selected) < 2:
        return selected
    roles = [core.COUNCIL_ROLES[i % len(core.COUNCIL_ROLES)] for i in range(len(selected))]
    def total(order: tuple[str, ...]) -> int:
        return sum(_role_affinity(profiles[name], roles[i]) for i, name in enumerate(order))
    if len(selected) <= 7:
        best = tuple(selected)
        best_score = total(best)
        for order in itertools.permutations(selected):
            score = total(order)
            if score > best_score:
                best, best_score = order, score
        return list(best)
    remaining = list(selected)
    out: list[str] = []
    for role in roles:
        best = max(remaining, key=lambda name: _role_affinity(profiles[name], role))
        out.append(best)
        remaining.remove(best)
    return out


def _reliability_rank(profile: dict[str, Any]) -> float:
    stats = profile.get("reliability") or {}
    rate = stats.get("round1_response_rate")
    invited = int(stats.get("invited") or 0)
    if rate is None or invited < 2:
        return 0.5
    return float(rate)


def select_candidates(v6: Any, base_choose: Any, *, allow_cloud: bool, max_agents: int,
                      allow_unknown: bool, context: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    registry = load_registry()
    base_names, reasons = base_choose(
        allow_cloud=allow_cloud,
        max_agents=10000,
        allow_unknown=allow_unknown,
    )
    cfgs = v6.core.load_agents_config()
    online = set(v6.core.online_agents(v6.core.fetch_status())) if v6.core.server_alive() else set()
    profiles: dict[str, dict[str, Any]] = {}
    candidates: list[str] = []
    base_index = {name: i for i, name in enumerate(base_names)}
    for name in base_names:
        profile = merged_profile(v6, name, cfgs.get(name), online, registry)
        profiles[name] = profile
        if not profile["enabled"]:
            reasons[name] = "excluded-disabled"
            continue
        if context.get("free_only") and profile["cost"] not in {"free", "local"}:
            reasons[name] = f"excluded-cost:{profile['cost']}"
            continue
        candidates.append(name)

    hard = set(context.get("required", []))
    soft = set(context.get("preferred", [])) | set(context.get("inferred", []))
    uncovered_hard = set(hard)
    uncovered_soft = set(soft)
    chosen: list[str] = []
    limit = max(1, int(max_agents))

    while candidates and len(chosen) < limit:
        def score(name: str) -> tuple[Any, ...]:
            p = profiles[name]
            caps = set(p["capabilities"])
            hard_gain = len(caps & uncovered_hard)
            soft_gain = len(caps & uncovered_soft)
            return (
                -hard_gain,
                -soft_gain,
                COST_RANK.get(p["cost"], 9),
                0 if p["online"] else 1,
                SPEED_RANK.get(p["speed"], 9),
                -_reliability_rank(p),
                base_index.get(name, 9999),
                name.lower(),
            )
        name = min(candidates, key=score)
        candidates.remove(name)
        chosen.append(name)
        caps = set(profiles[name]["capabilities"])
        uncovered_hard -= caps
        uncovered_soft -= caps

    missing = sorted(uncovered_hard)
    if missing and not context.get("best_effort"):
        raise v6.core.KaigiError(
            "明示capabilityを満たすcastを作れません: " + ", ".join(missing) +
            "。`kaigi caps` / `kaigi caps set NAME ...` / --best-effort-capabilities を確認してください。"
        )

    chosen = _arrange_for_roles(v6.core, chosen, profiles)
    coverage = sorted((hard | soft) - (uncovered_hard | uncovered_soft))
    context.update({
        "selected": list(chosen),
        "coverage": coverage,
        "missing_required": missing,
        "profiles": {name: profiles[name] for name in chosen},
        "registry_sha256": registry_hash(registry),
        "selection_policy": SELECTION_POLICY,
    })
    for name in chosen:
        p = profiles[name]
        cap_text = ",".join(p["capabilities"])
        reasons[name] = f"{reasons.get(name, p['classification'])} caps={cap_text} cost={p['cost']} speed={p['speed']}"
    return chosen, reasons


def _plan_for_explicit(v6: Any, names: list[str], context: dict[str, Any]) -> None:
    registry = load_registry()
    cfgs = v6.core.load_agents_config()
    online = set(v6.core.online_agents(v6.core.fetch_status())) if v6.core.server_alive() else set()
    profiles = {name: merged_profile(v6, name, cfgs.get(name), online, registry) for name in names}
    covered = set().union(*(set(p["capabilities"]) for p in profiles.values())) if profiles else set()
    missing = sorted(set(context.get("required", [])) - covered)
    if missing and not context.get("best_effort"):
        raise v6.core.KaigiError("明示agentが必要capabilityを満たしません: " + ", ".join(missing))
    context.update({
        "selected": list(names),
        "coverage": sorted((set(context.get("required", [])) | set(context.get("preferred", [])) | set(context.get("inferred", []))) & covered),
        "missing_required": missing,
        "profiles": profiles,
        "registry_sha256": registry_hash(registry),
        "selection_policy": SELECTION_POLICY + ":explicit",
    })


def _context_from_args(topic: str, args: Any) -> dict[str, Any]:
    required = parse_caps(getattr(args, "need", []))
    preferred = parse_caps(getattr(args, "prefer", []))
    inferred = [] if bool(getattr(args, "no_cap_infer", False)) else infer_topic(topic)
    return {
        "schema": PLAN_SCHEMA,
        "topic": topic,
        "required": required,
        "preferred": preferred,
        "inferred": inferred,
        "free_only": bool(getattr(args, "free_only", False)),
        "best_effort": bool(getattr(args, "best_effort_capabilities", False)),
    }


def _snapshot_plan(plan: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema", "selection_policy", "required", "preferred", "inferred", "free_only",
        "best_effort", "selected", "coverage", "missing_required", "profiles", "registry_sha256",
    )
    return {key: plan[key] for key in keys if key in plan}


def apply_core(core: Any) -> None:
    if getattr(core, "_kaigi_caps_new_run_wrapped", False):
        return
    original = core.new_run
    def wrapped(topic: str, channel: str, kind: str, **extra: Any) -> dict[str, Any]:
        plan = _ACTIVE_PLAN.get()
        if kind == "council" and plan:
            extra.setdefault("capability_plan", _snapshot_plan(plan))
        return original(topic, channel, kind, **extra)
    core.new_run = wrapped
    core._kaigi_caps_new_run_wrapped = True


def _add_decide_args(parser: Any) -> Any:
    for action in getattr(parser, "_actions", []):
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict) or "decide" not in choices:
            continue
        decide = choices["decide"]
        existing = {opt for a in getattr(decide, "_actions", []) for opt in getattr(a, "option_strings", [])}
        if "--need" not in existing:
            decide.add_argument("--need", action="append", default=[], help="必須capability。comma区切り/複数指定可")
            decide.add_argument("--prefer", action="append", default=[], help="優先capability。満たせなくても継続")
            decide.add_argument("--free-only", action="store_true", help="cost=free/localのagentだけでcast")
            decide.add_argument("--no-cap-infer", action="store_true", help="議題からのcapability自動推定を無効化")
            decide.add_argument("--best-effort-capabilities", action="store_true", help="明示--need未充足でも最善castで続行")
        break
    return parser


def apply_v6(v6: Any) -> None:
    apply_core(v6.core)
    if not getattr(v6, "_kaigi_caps_choose_wrapped", False):
        base_choose = v6.choose_auto_agents
        def choose(*, allow_cloud: bool, max_agents: int, allow_unknown: bool = False):
            context = _ACTIVE_PLAN.get()
            if not context:
                return base_choose(allow_cloud=allow_cloud, max_agents=max_agents, allow_unknown=allow_unknown)
            return select_candidates(
                v6, base_choose,
                allow_cloud=allow_cloud, max_agents=max_agents, allow_unknown=allow_unknown,
                context=context,
            )
        v6.choose_auto_agents = choose
        v6._kaigi_caps_choose_wrapped = True

    if not getattr(v6, "_kaigi_caps_decide_wrapped", False):
        original_decide = v6.cmd_decide
        def decide(args: Any) -> int:
            topic = " ".join(args.topic).strip()
            context = _context_from_args(topic, args)
            explicit = v6.ops.split_names(args.agents)
            if explicit:
                _plan_for_explicit(v6, explicit, context)
            token = _ACTIVE_PLAN.set(context)
            try:
                return int(original_decide(args) or 0)
            finally:
                _ACTIVE_PLAN.reset(token)
        v6.cmd_decide = decide
        v6._kaigi_caps_decide_wrapped = True

    if not getattr(v6, "_kaigi_caps_parser_wrapped", False):
        original_parser = v6.build_parser
        def parser(*args: Any, **kwargs: Any) -> Any:
            return _add_decide_args(original_parser(*args, **kwargs))
        v6.build_parser = parser
        v6._kaigi_caps_parser_wrapped = True

    if not getattr(v6, "_kaigi_caps_packet_wrapped", False):
        original_packet = v6.build_packet
        def packet(run: dict[str, Any]) -> dict[str, Any]:
            out = original_packet(run)
            plan = run.get("capability_plan")
            if isinstance(plan, dict):
                out["capability_plan"] = plan
                out.pop("packet_sha256", None)
                out["packet_sha256"] = v6._sha(out)
                path = v6._packet_path(str(run["run_id"]))
                v6._atomic_json(path, out)
                run["decision_packet"] = {
                    "schema": v6.PACKET_SCHEMA,
                    "path": str(path),
                    "sha256": out["packet_sha256"],
                    "transcript_sha256": out["transcript_sha256"],
                }
                v6.core.save_run(run)
            return out
        v6.build_packet = packet
        v6._kaigi_caps_packet_wrapped = True

    if not getattr(v6, "_kaigi_caps_handoff_wrapped", False):
        original_handoff = v6._make_handoff
        def handoff(packet: dict[str, Any]) -> dict[str, Any]:
            out = original_handoff(packet)
            plan = packet.get("capability_plan")
            if isinstance(plan, dict):
                out["capability_plan"] = plan
                out.pop("handoff_sha256", None)
                out["handoff_sha256"] = v6._sha(out)
            return out
        v6._make_handoff = handoff
        v6._kaigi_caps_handoff_wrapped = True


def _all_profiles(v6: Any) -> list[dict[str, Any]]:
    registry = load_registry()
    cfgs = v6.core.load_agents_config()
    online = set(v6.core.online_agents(v6.core.fetch_status())) if v6.core.server_alive() else set()
    names = sorted((set(cfgs) | online | set((registry.get("agents") or {}).keys())) - SYSTEM_AGENTS)
    return [merged_profile(v6, name, cfgs.get(name), online, registry) for name in names]


def caps_cli(v6: Any, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kaigi caps", description="agent capability registry")
    sub = parser.add_subparsers(dest="command")
    ls = sub.add_parser("list", aliases=["ls"])
    ls.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")
    setp = sub.add_parser("set")
    setp.add_argument("name")
    setp.add_argument("capabilities", nargs="+")
    setp.add_argument("--cost", choices=list(COST_RANK))
    setp.add_argument("--speed", choices=list(SPEED_RANK))
    setp.add_argument("--context", type=int, dest="context_tokens")
    setp.add_argument("--disable", action="store_true")
    unset = sub.add_parser("unset", aliases=["remove", "rm"])
    unset.add_argument("name")
    infer = sub.add_parser("infer")
    infer.add_argument("topic", nargs="+")
    infer.add_argument("--json", action="store_true")
    pathp = sub.add_parser("path")

    args = parser.parse_args(argv or [])
    command = args.command or "list"
    try:
        if command in {"list", "ls"}:
            profiles = _all_profiles(v6)
            if getattr(args, "json", False):
                print(json.dumps({"schema": REGISTRY_SCHEMA, "path": str(REGISTRY_PATH), "agents": profiles}, ensure_ascii=False, indent=2))
            else:
                print(f"registry: {REGISTRY_PATH}")
                if not profiles:
                    print("agents  : none")
                for p in profiles:
                    rate = p["reliability"].get("round1_response_rate")
                    reliability = "-" if rate is None else f"{rate:.0%}"
                    print(f"{p['name']:<16} {p['classification']:<20} cost={p['cost']:<7} speed={p['speed']:<7} r1={reliability:<4} caps={','.join(p['capabilities'])}")
            return 0
        if command == "show":
            matches = {p["name"]: p for p in _all_profiles(v6)}
            if args.name not in matches:
                raise CapabilityError(f"agent profileが見つかりません: {args.name}")
            p = matches[args.name]
            print(json.dumps(p, ensure_ascii=False, indent=2) if args.json else f"{p['name']}: caps={','.join(p['capabilities'])} cost={p['cost']} speed={p['speed']} context={p['context_tokens']}")
            return 0
        if command == "set":
            registry = load_registry()
            agents = registry.setdefault("agents", {})
            old = agents.get(args.name, {}) if isinstance(agents.get(args.name), dict) else {}
            entry = dict(old)
            entry["capabilities"] = parse_caps(args.capabilities)
            if args.cost is not None:
                entry["cost"] = args.cost
            if args.speed is not None:
                entry["speed"] = args.speed
            if args.context_tokens is not None:
                if args.context_tokens <= 0:
                    raise CapabilityError("--context は正の整数にしてください。")
                entry["context_tokens"] = args.context_tokens
            if args.disable:
                entry["enabled"] = False
            else:
                entry.setdefault("enabled", True)
            entry["updated_at"] = dt.datetime.now().astimezone().isoformat()
            agents[args.name] = entry
            save_registry(registry)
            print(f"✓ {args.name}: caps={','.join(entry['capabilities'])} cost={entry.get('cost', 'unknown')} speed={entry.get('speed', 'unknown')}")
            return 0
        if command in {"unset", "remove", "rm"}:
            registry = load_registry()
            agents = registry.setdefault("agents", {})
            existed = args.name in agents
            agents.pop(args.name, None)
            save_registry(registry)
            print(("✓ removed " if existed else "- not registered ") + args.name)
            return 0
        if command == "infer":
            topic = " ".join(args.topic)
            caps = infer_topic(topic)
            if args.json:
                print(json.dumps({"topic": topic, "inferred": caps}, ensure_ascii=False, indent=2))
            else:
                print("inferred: " + (", ".join(caps) if caps else "general"))
            return 0
        if command == "path":
            print(REGISTRY_PATH)
            return 0
    except CapabilityError as exc:
        v6.core.eprint(f"{v6.core.RED}エラー:{v6.core.RESET} {exc}")
        return 1
    return 1


def cast_cli(v6: Any, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kaigi cast", description="会議を開始せずcapability-aware castだけ計算")
    parser.add_argument("topic", nargs="+")
    parser.add_argument("--need", action="append", default=[])
    parser.add_argument("--prefer", action="append", default=[])
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--allow-unknown", action="store_true")
    parser.add_argument("--max-agents", type=int, default=getattr(v6, "DEFAULT_MAX_AGENTS", 4))
    parser.add_argument("--min-agents", type=int, default=getattr(v6, "DEFAULT_MIN_AGENTS", 2))
    parser.add_argument("--free-only", action="store_true")
    parser.add_argument("--no-cap-infer", action="store_true")
    parser.add_argument("--best-effort-capabilities", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv or [])
    topic = " ".join(args.topic).strip()
    context = _context_from_args(topic, args)
    token = _ACTIVE_PLAN.set(context)
    try:
        selected, reasons = v6.choose_auto_agents(
            allow_cloud=args.allow_cloud,
            max_agents=args.max_agents,
            allow_unknown=args.allow_unknown,
        )
    except (CapabilityError, v6.core.KaigiError) as exc:
        v6.core.eprint(f"{v6.core.RED}エラー:{v6.core.RESET} {exc}")
        return 1
    finally:
        _ACTIVE_PLAN.reset(token)
    if len(selected) < max(1, args.min_agents):
        v6.core.eprint(f"{v6.core.RED}エラー:{v6.core.RESET} cast不足: {len(selected)}/{args.min_agents}")
        return 1
    output = {"topic": topic, **_snapshot_plan(context), "reasons": {name: reasons.get(name) for name in selected}}
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("topic    : " + topic)
        print("need     : " + (", ".join(context["required"]) if context["required"] else "none"))
        print("inferred : " + (", ".join(context["inferred"]) if context["inferred"] else "none"))
        print("cast     : " + ", ".join(selected))
        print("coverage : " + (", ".join(context.get("coverage", [])) if context.get("coverage") else "general"))
        roles = [v6.core.COUNCIL_ROLES[i % len(v6.core.COUNCIL_ROLES)] for i in range(len(selected))]
        for i, name in enumerate(selected):
            p = context["profiles"][name]
            print(f"  {roles[i]:<12} -> {name:<16} cost={p['cost']:<7} speed={p['speed']:<7} caps={','.join(p['capabilities'])}")
    return 0
