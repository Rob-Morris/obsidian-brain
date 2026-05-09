#!/usr/bin/env python3
"""Repair-specific metadata and guidance helpers."""

from __future__ import annotations

from pathlib import Path
import shlex

from _lifecycle_common import DEFAULT_MANAGED_RUNTIME_LAUNCHER, find_repair_launcher


MANAGED_RUNTIME_ENV = "BRAIN_REPAIR_MANAGED"
BOOTSTRAP_SUMMARY_ENV = "BRAIN_REPAIR_BOOTSTRAP_SUMMARY"
REPAIR_SCRIPT_REL = Path(".brain-core/scripts/repair.py")

REPAIR_SCOPES = {
    "mcp": {
        "description": "Repair the vault-local managed runtime and project MCP registrations.",
        "check_message": "Brain MCP project registration drift detected for this vault.",
    },
    "router": {
        "description": "Rebuild the compiled router cache.",
        "check_message": "Compiled router is missing, stale, or unreadable.",
    },
    "index": {
        "description": "Rebuild the retrieval index cache.",
        "check_message": "Retrieval index is missing, stale, or unreadable.",
    },
    "registry": {
        "description": "Repair the current vault's local workspace registry state.",
        "check_message": "Local workspace registry state is malformed or needs normalisation.",
    },
    "semantic": {
        "description": "Repair semantic runtime provisioning and embeddings sidecars for this vault.",
        "check_message": "Semantic retrieval is configured on but the local semantic runtime is unavailable or stale.",
    },
}


def build_repair_command(
    vault_root: str | Path,
    scope: str,
    *,
    launcher: str | None = None,
    json_mode: bool = False,
    dry_run: bool = False,
) -> str:
    """Return an exact repair command for the given scope."""
    vault_root = Path(vault_root).resolve()
    script_path = vault_root / REPAIR_SCRIPT_REL
    launcher = launcher or find_repair_launcher() or DEFAULT_MANAGED_RUNTIME_LAUNCHER
    parts = [
        shlex.quote(launcher),
        shlex.quote(str(script_path)),
        scope,
        "--vault",
        shlex.quote(str(vault_root)),
    ]
    if dry_run:
        parts.append("--dry-run")
    if json_mode:
        parts.append("--json")
    return " ".join(parts)


def build_repair_metadata(vault_root: str | Path, scope: str) -> dict:
    """Return structured repair guidance for a compliance finding."""
    meta = REPAIR_SCOPES[scope]
    return {
        "scope": scope,
        "description": meta["description"],
        "command": build_repair_command(vault_root, scope),
    }


def attach_repair_guidance(finding: dict, vault_root: str | Path, scope: str) -> dict:
    """Attach structured + human repair guidance to a finding dict."""
    metadata = build_repair_metadata(vault_root, scope)
    finding["repair"] = metadata
    finding.setdefault("fix", f"Run `{metadata['command']}`")
    return finding
