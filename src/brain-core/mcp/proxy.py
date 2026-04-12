#!/usr/bin/env python3
"""Deprecated proxy entrypoint shim.

This file remains only as a temporary compatibility bridge for older MCP
configs that still launch `.brain-core/mcp/proxy.py`. Keep it bootstrap-only
and remove it after the shim support window closes.
"""

from __future__ import annotations

import os
import sys


_WARNED = False


def _warn_once() -> None:
    global _WARNED
    if not _WARNED:
        print(
            "Warning: `.brain-core/mcp/proxy.py` is deprecated. "
            "Re-run `.brain-core/scripts/init.py` to update the MCP launch "
            "config to `python -m brain_mcp.proxy`.",
            file=sys.stderr,
        )
        _WARNED = True


def _package_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _ensure_pythonpath() -> None:
    package_root = _package_root()
    existing = os.environ.get("PYTHONPATH", "")
    if existing and package_root in existing.split(os.pathsep):
        return
    parts = [package_root]
    if existing:
        parts.append(existing)
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def _rewrite_server_target(server_target: str) -> str:
    normalized = os.path.normpath(server_target)
    suffix = os.path.join(".brain-core", "mcp", "server.py")
    if normalized.endswith(suffix) or normalized == suffix:
        return "brain_mcp.server"
    return server_target


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <python_path> <server_target>", file=sys.stderr)
        sys.exit(1)

    _warn_once()
    _ensure_pythonpath()
    sys.path.insert(0, _package_root())

    from brain_mcp import proxy as real_proxy

    sys.argv = [sys.argv[0], sys.argv[1], _rewrite_server_target(sys.argv[2])]
    real_proxy.main()


if __name__ == "__main__":
    main()
