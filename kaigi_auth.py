#!/usr/bin/env python3
"""Local agentchattr auth discovery for kaigi.

agentchattr generates an in-memory browser session token on every server start and
injects it into the localhost index page. kaigi may run from a detached relay daemon
that does not inherit the interactive shell's auth environment, so it discovers that
session token only from a loopback agentchattr endpoint and keeps it in memory.

Explicit auth environment variables still win. No token is written to disk and no
non-loopback server is scraped for credentials. Browser/session credentials are never
synthesized into registered-agent Bearer credentials.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

AUTH_POLICY = "local-session-discovery-v3-identity-separated"
_TOKEN_RE = re.compile(r"window\.__SESSION_TOKEN__\s*=\s*(\"[0-9a-fA-F]{64}\")\s*;")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _explicit_env() -> tuple[str | None, str]:
    bearer = os.environ.get("KAIGI_BEARER_TOKEN") or os.environ.get("AGENTCHATTR_AGENT_TOKEN")
    if bearer and bearer.strip():
        return bearer.strip(), "bearer-env"
    session = os.environ.get("KAIGI_TOKEN") or os.environ.get("AGENTCHATTR_TOKEN")
    if session and session.strip():
        return session.strip(), "session-env"
    return None, "none"


def _is_loopback_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in _LOOPBACK


def discover_local_session_token(server_url: str, timeout: float = 1.5) -> str | None:
    """Read the current agentchattr browser session token from its loopback index."""
    url = server_url.rstrip("/")
    if not _is_loopback_url(url):
        return None
    req = urllib.request.Request(url + "/", method="GET", headers={"Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as resp:
            raw = resp.read(1_000_000)
    except Exception:
        return None
    text = raw.decode("utf-8", errors="replace")
    match = _TOKEN_RE.search(text)
    if not match:
        return None
    try:
        token = json.loads(match.group(1))
    except Exception:
        return None
    return token if isinstance(token, str) and len(token) == 64 else None


def auth_status_line(core: Any) -> str:
    """Return a token-safe status line proving which local auth source resolves."""
    try:
        token, source = core.resolve_token()
    except Exception:
        token, source = None, "error"
    return f"auth    : {'✓ ' if token else 'MISSING '}{source}"


def apply_core(core: Any) -> None:
    if getattr(core, "_kaigi_local_auth_applied", False):
        return
    original_resolve = core.resolve_token

    def resolve_token() -> tuple[str | None, str]:
        token, source = _explicit_env()
        if token:
            return token, source

        discovered = discover_local_session_token(str(core.SERVER_URL))
        if discovered:
            return discovered, "local-index-session"

        return original_resolve()

    # Keep the core header contract intact: registered-agent Bearer stays
    # Authorization; session credentials stay X-Session-Token. Transport routing
    # is handled separately by kaigi_transport and must not collapse identities.
    core.resolve_token = resolve_token
    core.LOCAL_AUTH_POLICY = AUTH_POLICY
    core._kaigi_local_auth_applied = True
