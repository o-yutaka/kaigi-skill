#!/usr/bin/env python3
"""Collision-safe atomic JSON writes for Kaigi state.

The legacy core uses a fixed sibling ``.tmp`` name.  That is safe with one
writer, but Council progress/reconcile paths can save the same run from more
than one thread/process.  Use a unique temporary file per write, fsync it,
then atomically replace the destination.  This module is applied by the public
``kaigi`` shim so the frozen core file does not need to be rewritten.
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
from typing import Any

ATOMIC_POLICY = "unique-temp-replace-v1"


def atomic_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(6)}"
    )
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def apply_core(core: Any) -> None:
    core.atomic_json = atomic_json
