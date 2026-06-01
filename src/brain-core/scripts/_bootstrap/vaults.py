#!/usr/bin/env python3
"""Launcher-safe Brain vault discovery helpers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from _common import find_vault_root as _find_common_vault_root
from _common import is_vault_root


def make_vault_parent_parser() -> argparse.ArgumentParser:
    """Return a shared parent parser that declares ``--vault`` at the top level.

    Pass this as ``parents=[make_vault_parent_parser()]`` to every
    ``ArgumentParser`` (top-level and each leaf subparser) in scripts that are
    dispatched by ``cli/brain``.  The wrapper injects ``--vault <path>`` at the
    front of the argument list; accepting it at the top level ensures the
    injection always parses regardless of which subcommand follows.

    ``default=argparse.SUPPRESS`` is load-bearing: an absent ``--vault`` must
    not set ``args.vault`` at all, so a top-level value cannot be clobbered by
    a copy-back from a subparser that never received ``--vault`` on its own.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--vault",
        default=argparse.SUPPRESS,
        help="Path to the Brain vault (default: auto-detect from script location or BRAIN_VAULT_ROOT).",
    )
    return parent


def find_vault_root(vault_arg: str | None = None) -> Path:
    """Resolve a Brain vault root from arg, env, cwd, or script location."""
    if vault_arg:
        return _find_common_vault_root(vault_arg)

    env_root = os.environ.get("BRAIN_VAULT_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if is_vault_root(candidate):
            return candidate

    return _find_common_vault_root()
