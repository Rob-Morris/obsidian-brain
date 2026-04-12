#!/usr/bin/env python3
"""Deprecated server entrypoint shim.

This file remains only as a temporary compatibility bridge for older MCP
configs that still launch `.brain-core/mcp/server.py`. Keep it bootstrap-only
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
            "Warning: `.brain-core/mcp/server.py` is deprecated. "
            "Re-run `.brain-core/scripts/init.py` to update the MCP launch "
            "config to `python -m brain_mcp.proxy` / `python -m brain_mcp.server`.",
            file=sys.stderr,
        )
        _WARNED = True


def _package_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    _warn_once()
    sys.path.insert(0, _package_root())

    from brain_mcp import server as real_server

    real_server.main()


if __name__ == "__main__":
    main()
