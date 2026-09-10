#!/usr/bin/env python3
"""AgentChattr auth bootstrap for kaigi.

AgentChattr generates a fresh browser session token on every server start and
injects it into the loopback-only web root as window.__SESSION_TOKEN__. kaigi
uses that documented local surface when no explicit agent Bearer/session token
was supplied, so background relay workers survive AgentChattr restarts without
copy/pasting credentials.

The fallback is deliberately restricted to loopback AgentChattr URLs. It never
reads browser storage, agent bearer tokens, or provider credentials.
"""
from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
from typing import Any

AUTH_POLICY = "agentchattr-loopback-session-bootstrap-v1"
_TOKEN_RE = re.compile(r"window\.__SESSION_TOKEN__\s*=\s*['\"]([0-9a-fA-F]{64})['\"]")
_MAX_ROOT_BYTES = 1024 * 1024


def _loopback_session_token(core: Any) -> str | None:
    try:
        parsed = urllib.parse.urlparse(core.SERVER_URL)
        host = (parsed.hostname or "").lower()
        if host not in {str(x).lower() for x in core.LOCAL_HOSTS}:
            return None
        root = core.SERVER_URL.rstrip("/") + "/"
        req = urllib.request.Request(root, method="GET", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=1.2) as resp:
            raw = resp.read(_MAX_ROOT_BYTES + 1)
        if len(raw) > _MAX_ROOT_BYTES:
            return None
        match = _TOKEN_RE.search(raw.decode("utf-8", errors="replace"))
        return match.group(1) if match else None
    except Exception:
        return None


def apply_core(core: Any) -> None:
    if getattr(core, "_kaigi_auth_bootstrap_applied", False):
        return

    def resolve_token() -> tuple[str | None, str]:
        bearer = os.environ.get("KAIGI_BEARER_TOKEN") or os.environ.get("AGENTCHATTR_AGENT_TOKEN")
        if bearer and bearer.strip():
            return bearer.strip(), "bearer-env"

        session = os.environ.get("KAIGI_TOKEN") or os.environ.get("AGENTCHATTR_TOKEN")
        if session and session.strip():
            return session.strip(), "session-env"

        # Prefer the currently running server's token over a possibly stale log.
        live = _loopback_session_token(core)
        if live:
            return live, "server-root"

        if core.LOG_FILE.is_file():
            try:
                for line in reversed(core.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()):
                    if "Session token:" in line:
                        token = line.split("Session token:", 1)[1].strip().split()[0]
                        if token:
                            return token, "server-log"
            except OSError:
                pass
        return None, "none"

    core.resolve_token = resolve_token
    core.AUTH_POLICY = AUTH_POLICY
    core._kaigi_auth_bootstrap_applied = True
