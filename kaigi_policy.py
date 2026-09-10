#!/usr/bin/env python3
"""Provider-neutral runtime policy for kaigi v6.

The meeting control plane must not depend on, prefer, or require a named AI
provider. Provider-specific CLIs/APIs remain optional adapters.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

POLICY_SCHEMA = "kaigi.provider_policy.v1"
POLICY_ID = "provider-neutral-v1"


def _names(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        name = str(value).strip().lstrip("@")
        if name and name not in out:
            out.append(name)
    return out


def choose_synth(core: Any, targets: list[str], requested: str | None = None) -> str:
    """Provider-neutral synthesizer selection.

    explicit --synth > KAIGI_SYNTH_AGENT > policy.
    Default balanced policy chooses the least-recently-used successful FINAL
    synthesizer from the current participants. Ties preserve participant order.
    """
    candidates = _names(targets)
    if not candidates:
        raise core.KaigiError("synth候補がいません。")

    explicit = (requested or os.environ.get("KAIGI_SYNTH_AGENT") or "").strip().lstrip("@")
    if explicit:
        if explicit not in candidates:
            raise core.KaigiError(f"synth agent が参加者にいません: {explicit}")
        return explicit

    mode = (os.environ.get("KAIGI_SYNTH_POLICY") or "balanced").strip().lower()
    if mode == "first":
        return candidates[0]
    if mode != "balanced":
        raise core.KaigiError("KAIGI_SYNTH_POLICY は balanced または first を指定してください。")

    last_seen: dict[str, int | None] = {name: None for name in candidates}
    try:
        history = core.list_runs(limit=200)
    except Exception:
        history = []
    for index, run in enumerate(history):
        if not isinstance(run, dict) or run.get("state") != "complete":
            continue
        synth = str(run.get("final_sender") or run.get("synth") or "")
        if synth in last_seen and last_seen[synth] is None:
            last_seen[synth] = index

    never = [name for name in candidates if last_seen[name] is None]
    if never:
        return never[0]
    return max(candidates, key=lambda name: int(last_seen[name] or 0))


def choose_auto_agents(v6: Any, *, allow_cloud: bool, max_agents: int,
                       allow_unknown: bool = False) -> tuple[list[str], dict[str, str]]:
    """Select safe-auto participants by runtime properties, never brand name.

    Priority is operational only: already-online before offline; then local API,
    CLI, explicitly-allowed cloud API, and explicitly-allowed unknown online.
    Alphabetical name is only a deterministic tie-breaker.
    """
    cfgs = v6.core.load_agents_config()
    online = set(v6.core.online_agents(v6.core.fetch_status())) if v6.core.server_alive() else set()
    names = set(cfgs) | online
    reasons: dict[str, str] = {}
    eligible: list[tuple[tuple[int, int, str], str]] = []
    class_rank = {"api-local": 0, "cli": 1, "api-cloud": 2, "online-unclassified": 3}

    for name in sorted(names):
        if name in {"user", "system", "telegram-bot"}:
            reasons[name] = "excluded-system"
            continue
        cfg = cfgs.get(name)
        cls = v6._classification(name, cfg, online)
        if cls == "api-cloud" and not allow_cloud:
            reasons[name] = "excluded-cloud"
            continue
        if cls == "unclassified":
            reasons[name] = "excluded-unconfigured"
            continue
        if cls == "online-unclassified" and not allow_unknown:
            reasons[name] = "excluded-unclassified-online"
            continue
        reason = cls + (":online" if name in online else ":offline")
        reasons[name] = reason
        score = (0 if name in online else 1, class_rank.get(cls, 99), name.lower())
        eligible.append((score, name))

    eligible.sort(key=lambda item: item[0])
    selected = [name for _, name in eligible[:max(1, max_agents)]]
    return selected, reasons


def _neutral_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    replacements = (
        ("claude,codex,chatgpt", "agent-a,agent-b,agent-c"),
        ("claude,codex", "agent-a,agent-b"),
        ("claude codex", "agent-a agent-b"),
        ("@claude", "@agent-a"),
        ("--synth claude", "--synth agent-a"),
        ("planner=claude", "planner=agent-a"),
    )
    for old, new in replacements:
        value = value.replace(old, new)
    return value


def scrub_parser(parser: Any) -> Any:
    parser.description = _neutral_text(getattr(parser, "description", None))
    parser.epilog = _neutral_text(getattr(parser, "epilog", None))
    for action in getattr(parser, "_actions", []):
        action.help = _neutral_text(getattr(action, "help", None))
        metavar = getattr(action, "metavar", None)
        if isinstance(metavar, str):
            action.metavar = _neutral_text(metavar)
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for child in choices.values():
                if isinstance(child, argparse.ArgumentParser):
                    scrub_parser(child)
    return parser


def _wrap_parser(module: Any) -> None:
    if getattr(module, "_kaigi_neutral_parser_wrapped", False):
        return
    original = module.build_parser

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return scrub_parser(original(*args, **kwargs))

    module.build_parser = wrapped
    module._kaigi_neutral_parser_wrapped = True


def apply_core(core: Any) -> None:
    core.SYNTH_PRIORITY = []

    def neutral_choose(targets: list[str], requested: str | None = None) -> str:
        return choose_synth(core, targets, requested)

    core.choose_synth = neutral_choose
    core.PROVIDER_POLICY = POLICY_ID
    _wrap_parser(core)


def apply_ops(ops: Any) -> None:
    _wrap_parser(ops)
    if getattr(ops, "_kaigi_neutral_launch_wrapped", False):
        return
    original = ops.cmd_launch

    def neutral_launch(args: Any) -> int:
        if not list(getattr(args, "names", []) or []) and not bool(getattr(args, "all", False)):
            raise ops.core.KaigiError("起動するCLI agentを指定してください。例: kaigi launch agent-a agent-b")
        return int(original(args) or 0)

    ops.cmd_launch = neutral_launch
    ops._kaigi_neutral_launch_wrapped = True


def apply_v6(v6: Any) -> None:
    """Disable the legacy brand priority used by safe-auto."""
    v6.PREFERRED_AGENTS = []

    def neutral_auto(*, allow_cloud: bool, max_agents: int,
                     allow_unknown: bool = False) -> tuple[list[str], dict[str, str]]:
        return choose_auto_agents(v6, allow_cloud=allow_cloud, max_agents=max_agents,
                                  allow_unknown=allow_unknown)

    v6.choose_auto_agents = neutral_auto
    v6.PROVIDER_POLICY = POLICY_ID
    _wrap_parser(v6)


def snapshot() -> dict[str, Any]:
    return {
        "schema": POLICY_SCHEMA,
        "policy": POLICY_ID,
        "provider_neutral": True,
        "required_providers": [],
        "synth": {
            "default": os.environ.get("KAIGI_SYNTH_POLICY", "balanced"),
            "pinned_agent": os.environ.get("KAIGI_SYNTH_AGENT"),
            "brand_priority": False,
        },
        "safe_auto": {
            "brand_priority": False,
            "selection": ["online-state", "runtime-type", "deterministic-name-tiebreak"],
            "cloud_implicit": False,
        },
        "provider_specific_integrations": "optional-adapters",
    }


def policy_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kaigi policy", description="provider-neutral policyを表示")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv or [])
    data = snapshot()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"policy            : {data['policy']}")
        print("provider-neutral  : YES")
        print("required providers: none")
        print(f"synth             : {data['synth']['default']} (brand priority: off)")
        print("safe-auto         : online/runtime-type based (brand priority: off)")
        print("cloud implicit    : off")
    return 0
