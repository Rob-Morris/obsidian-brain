#!/usr/bin/env python3
"""Machine-level Brain target resolver entry point.

This script is deployed into ``~/.brain/resolution-runtime`` and run by the
``brain`` CLI before any Brain has been selected. It is deliberately narrow:
run the pure resolver, emit a machine-readable target or degraded
``session_resolution`` payload, and never mutate workspace or registry state.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from _bootstrap.workspace_binding import WorkspaceBindingError, resolve_brain_target


RESOLUTION_RUNTIME_VERSION = "0.1.0"


def _context(start_dir: Path) -> dict[str, Any]:
    workspace_env = os.environ.get("BRAIN_WORKSPACE_DIR") or None
    vault_root_env = os.environ.get("BRAIN_VAULT_ROOT") or None
    return {
        "workspace_env": workspace_env,
        "vault_root_env": vault_root_env,
        "start_dir": str(start_dir),
        "workspace_anchor_explicit": bool(workspace_env),
        "vault_root_explicit": bool(vault_root_env),
    }


def _recovery_for_error(code: str, context: dict[str, Any]) -> dict[str, str]:
    workspace_env = context.get("workspace_env")
    vault_root_env = context.get("vault_root_env")

    if code == "filesystem_access":
        return {
            "action": "Inspect the machine Brain registry/default state, then rerun brain session.",
            "command": "brain doctor --actionable",
        }

    if workspace_env:
        if code == "no_brain" and vault_root_env:
            return {
                "action": "Repair this workspace binding or verify the explicit Brain vault root, then rerun brain session.",
                "command": f"brain setup workspace {workspace_env} --vault {vault_root_env}",
            }
        return {
            "action": "Re-bind or repair this workspace, then rerun brain session.",
            "command": f"brain setup workspace {workspace_env}",
        }

    if code == "stale_binding":
        return {
            "action": (
                "Re-bind/check the workspace if one is in scope, and check or clear "
                "the machine default Brain if the registry default is dangling."
            ),
            "command": "brain doctor --actionable",
        }

    if code == "no_brain":
        return {
            "action": "Bind this workspace to a Brain or set a machine default Brain, then rerun brain session.",
            "command": "brain setup workspace .",
        }

    return {
        "action": "Inspect the workspace binding and machine Brain registry, then rerun brain session.",
        "command": "brain doctor --actionable",
    }


def resolve_payload(*, start_dir: Path) -> dict[str, Any]:
    context = _context(start_dir)
    try:
        target = resolve_brain_target(
            workspace_env=context["workspace_env"],
            vault_root_env=context["vault_root_env"],
            start_dir=start_dir,
        )
    except WorkspaceBindingError as exc:
        return {
            "status": "degraded",
            "recovery_class": "session_resolution",
            "vault_root": None,
            "message": str(exc),
            "session_resolution": {
                "code": exc.code,
                "context": context,
            },
            "recovery": _recovery_for_error(exc.code, context),
            "resolution_runtime_version": RESOLUTION_RUNTIME_VERSION,
        }

    return {
        "status": "ok",
        "resolution_runtime_version": RESOLUTION_RUNTIME_VERSION,
        "target": {
            "kind": "local",
            "vault_root": target.vault_root,
            "workspace_dir": target.workspace_dir,
            "source": target.source,
        },
        "context": context,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the active Brain target from machine-level context."
    )
    parser.add_argument("--start-dir", default=os.getcwd(), help="Directory for cwd-walk resolution.")
    parser.add_argument("--json", action="store_true", help="Emit JSON payload.")
    args = parser.parse_args(argv)

    payload = resolve_payload(start_dir=Path(args.start_dir).resolve())
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if payload["status"] == "ok":
        target = payload["target"]
        print(target["vault_root"])
        return 0

    print(f"Error: {payload['message']}", file=sys.stderr)
    print(payload["recovery"]["action"], file=sys.stderr)
    print(payload["recovery"]["command"], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
