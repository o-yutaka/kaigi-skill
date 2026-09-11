#!/usr/bin/env python3
"""Kaigi-launched AgentChattr wrapper compatibility layer.

The upstream AgentChattr wrapper already owns agent identity, registration,
queue delivery and a local per-agent MCP identity proxy. This module keeps
that contract intact and adds narrow Kaigi runtime adapters without modifying
AgentChattr on disk.

For ClawCodex only, when no explicit ``mcp_inject`` is configured:
- keep AgentChattr's generated per-agent bearer token inside its local proxy;
- point ClawCodex at that loopback proxy from a Kaigi-owned workspace;
- approve only the ``agentchattr`` project MCP server in that workspace;
- never write the AgentChattr bearer token to ClawCodex config;
- never modify the user's project repository or global ClawCodex config.

For POSIX/tmux CLI wrappers, ``kaigi_delivery`` adds metadata-only delivery
receipts and a non-authorizing Codex hook-review readiness gate. It never
persists prompt text and never trusts hooks automatically.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import sys
import urllib.parse
from typing import Any

import kaigi_core as core
import kaigi_delivery as delivery

COMPAT_POLICY = "clawcodex-local-identity-proxy-v1"
SERVER_NAME = "agentchattr"
WORKSPACE_ROOT = core.STATE_DIR / "agent-workspaces"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _atomic_json(path: pathlib.Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "agent"


def _command_basename(command: Any) -> str:
    if not isinstance(command, str):
        return ""
    value = command.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value


def needs_clawcodex_compat(agent: str, cfg: dict[str, Any] | None) -> bool:
    """Use the adapter only for an uncustomized ClawCodex CLI definition."""
    cfg = cfg or {}
    if cfg.get("mcp_inject"):
        return False
    return agent.lower() == "clawcodex" or _command_basename(cfg.get("command")) == "clawcodex"


def _loopback_proxy_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in _LOOPBACK and bool(parsed.port)


def _resolve_original_cwd(root: pathlib.Path, cfg: dict[str, Any]) -> pathlib.Path:
    raw = str(cfg.get("cwd") or ".")
    path = pathlib.Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _workspace_for(agent: str) -> pathlib.Path:
    return WORKSPACE_ROOT / _safe_name(agent)


def prepare_clawcodex_workspace(
    workspace: pathlib.Path,
    *,
    proxy_url: str,
    original_cwd: pathlib.Path | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Write a token-free, Kaigi-owned ClawCodex MCP workspace.

    The project MCP URL is the short-lived local identity proxy. AgentChattr's
    bearer token remains inside that proxy process and therefore never lands in
    ``.mcp.json`` or ClawCodex's global config.
    """
    if not _loopback_proxy_url(proxy_url):
        raise RuntimeError("ClawCodex compat requires an http loopback identity-proxy URL")

    workspace.mkdir(parents=True, exist_ok=True)
    mcp_path = workspace / ".mcp.json"
    settings_path = workspace / ".clawcodex" / "settings.local.json"

    _atomic_json(
        mcp_path,
        {
            "mcpServers": {
                SERVER_NAME: {
                    "type": "http",
                    "url": proxy_url,
                }
            }
        },
    )

    existing: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}

    enabled = existing.get("enabledMcpjsonServers")
    if not isinstance(enabled, list):
        enabled = []
    enabled = [str(x) for x in enabled if isinstance(x, str)]
    if SERVER_NAME not in enabled:
        enabled.append(SERVER_NAME)
    existing["enabledMcpjsonServers"] = enabled
    # Never widen approval to unrelated project MCP servers.
    existing.pop("enableAllProjectMcpServers", None)

    if original_cwd is not None:
        original_text = str(original_cwd)
        extras = existing.get("additionalWorkingDirectories")
        if not isinstance(extras, list):
            extras = []
        extras = [str(x) for x in extras if isinstance(x, str)]
        if original_text not in extras and original_cwd != workspace:
            extras.append(original_text)
        existing["additionalWorkingDirectories"] = extras

    _atomic_json(settings_path, existing)
    return mcp_path, settings_path


def compat_shell_line(agent: str) -> str:
    """Launch upstream wrapper through this adapter in AgentChattr's venv."""
    py = core.python_bin()
    wrapper = core.HOME / "wrapper.py"
    helper = pathlib.Path(__file__).resolve()
    if not py or not wrapper.is_file():
        raise core.KaigiError("agentchattr wrapper.py またはvenvが見つかりません。")
    return (
        f"cd {shlex.quote(str(core.HOME))} && "
        f"{shlex.quote(str(py))} {shlex.quote(str(helper))} {shlex.quote(agent)}"
    )


def apply_ops(ops: Any) -> None:
    """Route kaigi's CLI-agent launcher through the compatibility shim."""
    ops.shell_line = compat_shell_line


def _load_upstream() -> tuple[Any, Any]:
    wrapper_path = core.HOME / "wrapper.py"
    config_path = core.HOME / "config_loader.py"
    if not wrapper_path.is_file() or not config_path.is_file():
        raise RuntimeError(f"AgentChattr wrapper/config loader not found under {core.HOME}")
    sys.path.insert(0, str(core.HOME))
    import config_loader  # type: ignore
    import wrapper  # type: ignore

    return config_loader, wrapper


def install_runtime_patch(config_loader: Any, wrapper: Any, selected_agent: str) -> None:
    """Patch one wrapper process; no global AgentChattr files are modified."""
    original_load = config_loader.load_config
    original_build = wrapper._build_provider_launch

    def compat_load(root: pathlib.Path | None = None) -> dict[str, Any]:
        config = original_load(root)
        agents = config.get("agents") if isinstance(config, dict) else None
        cfg = agents.get(selected_agent) if isinstance(agents, dict) else None
        if not isinstance(cfg, dict) or not needs_clawcodex_compat(selected_agent, cfg):
            return config

        cfg = dict(cfg)
        root_path = pathlib.Path(root) if root is not None else core.HOME
        original_cwd = _resolve_original_cwd(root_path, cfg)
        workspace = _workspace_for(selected_agent)
        workspace.mkdir(parents=True, exist_ok=True)
        cfg["cwd"] = str(workspace)
        cfg["_kaigi_clawcodex_compat"] = True
        cfg["_kaigi_original_cwd"] = str(original_cwd)
        agents[selected_agent] = cfg
        return config

    def compat_build(
        agent: str,
        agent_cfg: dict[str, Any],
        instance_name: str,
        data_dir: pathlib.Path,
        proxy_url: str | None,
        extra_args: list[str],
        env: dict[str, str],
        *,
        token: str = "",
        mcp_cfg: dict[str, Any] | None = None,
        project_dir: pathlib.Path | None = None,
    ) -> tuple[list[str], dict[str, str], dict[str, str], pathlib.Path | None]:
        if not agent_cfg.get("_kaigi_clawcodex_compat"):
            return original_build(
                agent, agent_cfg, instance_name, data_dir, proxy_url, extra_args, env,
                token=token, mcp_cfg=mcp_cfg, project_dir=project_dir,
            )
        if project_dir is None:
            raise RuntimeError("ClawCodex compat workspace was not resolved")
        if not _loopback_proxy_url(proxy_url):
            raise RuntimeError("AgentChattr did not provide a loopback identity proxy for ClawCodex")
        raw_original = str(agent_cfg.get("_kaigi_original_cwd") or "")
        original_cwd = pathlib.Path(raw_original) if raw_original else None
        mcp_path, _ = prepare_clawcodex_workspace(
            pathlib.Path(project_dir),
            proxy_url=str(proxy_url),
            original_cwd=original_cwd,
        )
        # No bearer token is injected into args/env/files. The local proxy owns it.
        launch_env = dict(env)
        return list(extra_args), launch_env, {}, mcp_path

    config_loader.load_config = compat_load
    wrapper._build_provider_launch = compat_build


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: kaigi_agent_compat.py AGENT [wrapper args...]", file=sys.stderr)
        return 2
    selected_agent = args[0]
    config_loader, wrapper = _load_upstream()
    install_runtime_patch(config_loader, wrapper, selected_agent)
    delivery.install_wrapper_patch(selected_agent)
    old_argv = sys.argv
    try:
        sys.argv = [str(core.HOME / "wrapper.py"), *args]
        result = wrapper.main()
        return int(result or 0)
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(main())
