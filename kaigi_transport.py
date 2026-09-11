#!/usr/bin/env python3
"""AgentChattr transport compatibility for kaigi.

Current AgentChattr deliberately separates two identities:
- browser/human control: session token over /ws
- registered agents: per-agent Bearer token over /api/send

kaigi is a control-plane client, not an agent identity.  For session-authenticated
control messages we therefore use the same WebSocket path as the browser.  An
explicit registered-agent bearer token keeps using /api/send.

Older AgentChattr releases accepted a session token on /api/send; that path is
kept as a compatibility probe and automatically switches to WebSocket only when
the server explicitly reports the new Bearer-only /api/send contract.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
from typing import Any

TRANSPORT_POLICY = "identity-separated-session-ws-v1"
_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_SESSION_SEND_MODE: str | None = None


def _requires_registered_bearer(exc: Exception) -> bool:
    return "missing Authorization: Bearer <token>" in str(exc)


def _host_header(host: str, port: int, scheme: str) -> str:
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default = 443 if scheme == "https" else 80
    return shown if port == default else f"{shown}:{port}"


def _client_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    first = 0x80 | (opcode & 0x0F)
    n = len(payload)
    if n <= 125:
        header = bytes((first, 0x80 | n))
    elif n <= 0xFFFF:
        header = bytes((first, 0x80 | 126)) + struct.pack("!H", n)
    else:
        header = bytes((first, 0x80 | 127)) + struct.pack("!Q", n)
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return header + mask + masked


class _Reader:
    def __init__(self, sock: socket.socket, initial: bytes = b""):
        self.sock = sock
        self.buf = bytearray(initial)

    def read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def frame(self) -> tuple[int, bool, bytes]:
        b1, b2 = self.read(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.read(8))[0]
        mask = self.read(4) if masked else b""
        payload = self.read(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, fin, payload


def _read_handshake(sock: socket.socket) -> tuple[int, dict[str, str], bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("websocket handshake closed")
        data.extend(chunk)
        if len(data) > 65536:
            raise ConnectionError("websocket handshake too large")
    head, rest = bytes(data).split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    parts = lines[0].split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise ConnectionError("invalid websocket status line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return int(parts[1]), headers, rest


def _newer(core: Any, msg: dict[str, Any], before_id: Any) -> bool:
    try:
        return int(core.msg_id(msg)) > int(before_id)
    except (TypeError, ValueError):
        return core.msg_id(msg) != before_id


def _session_ws_send(core: Any, token: str, text: str, channel: str, timeout: float = 6.0) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(str(core.SERVER_URL))
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK:
        raise core.KaigiError(
            "session WebSocket送信はloopback AgentChattrだけ許可します。"
            "remote serverではregistered-agent Bearerを明示してください。"
        )
    if parsed.scheme not in {"http", "https"}:
        raise core.KaigiError(f"未対応のAgentChattr URL scheme: {parsed.scheme}")

    previous = core.fetch_messages(limit=1, channel=channel)
    before_id = core.latest_id(previous, -1)

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    sock: socket.socket = raw_sock
    if parsed.scheme == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=host)
    sock.settimeout(timeout)

    base_path = parsed.path.rstrip("/")
    ws_path = (base_path + "/ws") or "/ws"
    query = urllib.parse.urlencode({"token": token})
    target = f"{ws_path}?{query}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host_header = _host_header(host, port, parsed.scheme)
    origin = f"{parsed.scheme}://{host_header}"
    request = (
        f"GET {target} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: {origin}\r\n"
        "\r\n"
    ).encode("ascii")

    try:
        sock.sendall(request)
        status, headers, leftover = _read_handshake(sock)
        expected = base64.b64encode(hashlib.sha1((key + _MAGIC).encode("ascii")).digest()).decode("ascii")
        if status != 101 or headers.get("sec-websocket-accept") != expected:
            raise core.KaigiError(f"AgentChattr WebSocket handshake失敗: HTTP {status}")

        event = json.dumps(
            {"type": "message", "text": text, "channel": channel},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        sock.sendall(_client_frame(event, opcode=0x1))

        reader = _Reader(sock, leftover)
        deadline = time.monotonic() + timeout
        fragments = bytearray()
        fragment_opcode: int | None = None
        while time.monotonic() < deadline:
            sock.settimeout(max(0.1, min(0.5, deadline - time.monotonic())))
            try:
                opcode, fin, payload = reader.frame()
            except socket.timeout:
                continue
            except ConnectionError:
                break

            if opcode == 0x9:
                sock.sendall(_client_frame(payload, opcode=0xA))
                continue
            if opcode == 0x8:
                break
            if opcode in {0x1, 0x2}:
                fragment_opcode = opcode
                fragments = bytearray(payload)
            elif opcode == 0x0 and fragment_opcode is not None:
                fragments.extend(payload)
            else:
                continue
            if not fin:
                continue

            complete_opcode = fragment_opcode
            complete = bytes(fragments)
            fragments.clear()
            fragment_opcode = None
            if complete_opcode != 0x1:
                continue
            try:
                incoming = json.loads(complete.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(incoming, dict) or incoming.get("type") != "message":
                continue
            msg = incoming.get("data")
            if not isinstance(msg, dict):
                continue
            if str(msg.get("text") or "") != text:
                continue
            if str(msg.get("channel") or core.DEFAULT_CHANNEL) != channel:
                continue
            if not _newer(core, msg, before_id):
                continue
            try:
                sock.sendall(_client_frame(struct.pack("!H", 1000), opcode=0x8))
            except Exception:
                pass
            return msg
    finally:
        try:
            sock.close()
        except Exception:
            pass

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        for msg in core.fetch_messages(since_id=before_id, channel=channel):
            if (
                str(msg.get("text") or "") == text
                and str(msg.get("channel") or core.DEFAULT_CHANNEL) == channel
                and _newer(core, msg, before_id)
            ):
                return msg
        time.sleep(0.05)
    raise core.KaigiError("AgentChattr WebSocket送信のdurable反映を確認できませんでした。")


def apply_core(core: Any) -> None:
    if getattr(core, "_kaigi_transport_applied", False):
        return
    legacy_send = core.send_message

    def send_message(text: str, channel: str = core.DEFAULT_CHANNEL) -> Any:
        global _SESSION_SEND_MODE
        token, source = core.resolve_token()
        if not token:
            raise core.KaigiError("AgentChattr認証tokenがありません。")

        if source == "bearer-env":
            return legacy_send(text, channel)

        if _SESSION_SEND_MODE != "websocket":
            try:
                result = legacy_send(text, channel)
                _SESSION_SEND_MODE = "http-session"
                return result
            except core.KaigiError as exc:
                if not _requires_registered_bearer(exc):
                    raise
                _SESSION_SEND_MODE = "websocket"

        return _session_ws_send(core, token, text, channel)

    core.send_message = send_message
    core.CONTROL_TRANSPORT_POLICY = TRANSPORT_POLICY
    core._kaigi_transport_applied = True
