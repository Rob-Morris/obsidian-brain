#!/usr/bin/env python3
"""Repair-specific metadata and guidance helpers."""

from __future__ import annotations

from pathlib import Path
import shlex

from _bootstrap.runtime import (
    BOOTSTRAP_SUMMARY_ENV,
    DEFAULT_MANAGED_RUNTIME_LAUNCHER,
    find_launcher_python,
)


REPAIR_SCRIPT_REL = Path(".brain-core/scripts/repair.py")

REPAIR_SCOPES = {
    "runtime": {
        "description": "Repair the central managed Brain runtime and its baseline packages via bootstrap, then verify the result.",
        "check_message": "Central managed Brain runtime is missing, unusable, or missing required baseline packages.",
    },
    "mcp": {
        "description": "Repair current-vault Claude/Codex MCP registration state against a usable managed runtime.",
        "check_message": "Brain MCP project registration drift detected for this vault.",
    },
    "router": {
        "description": "Rebuild the compiled router cache.",
        "check_message": "Compiled router is missing, stale, or unreadable.",
    },
    "lexical": {
        "description": "Rebuild the lexical retrieval index cache.",
        "check_message": "Lexical retrieval index is missing, stale, or unreadable.",
    },
    "registry": {
        "description": "Repair the current vault's local workspace registry state.",
        "check_message": "Local workspace registry state is malformed or needs normalisation.",
    },
    "frontmatter": {
        "description": "Repair duplicate artefact frontmatter blocks by merging nested frontmatter into the document frontmatter.",
        "check_message": "Artefact frontmatter is malformed and needs duplicate-frontmatter normalisation.",
    },
    "semantic": {
        "description": "Repair semantic runtime provisioning and embeddings sidecars for this vault.",
        "check_message": "Semantic retrieval is configured on but the local semantic runtime is unavailable or stale.",
    },
}


def build_repair_argv(
    vault_root: str | Path,
    scope: str,
    *,
    launcher: str | None = None,
    json_mode: bool = False,
    dry_run: bool = False,
) -> list[str]:
    """Return argv for the exact repair.py invocation for one scope."""
    vault_root = Path(vault_root).resolve()
    script_path = vault_root / REPAIR_SCRIPT_REL
    launcher = launcher or find_launcher_python() or DEFAULT_MANAGED_RUNTIME_LAUNCHER
    argv = [
        launcher,
        str(script_path),
        scope,
        "--vault",
        str(vault_root),
    ]
    if dry_run:
        argv.append("--dry-run")
    if json_mode:
        argv.append("--json")
    return argv


def build_repair_command(
    vault_root: str | Path,
    scope: str,
    *,
    launcher: str | None = None,
    json_mode: bool = False,
    dry_run: bool = False,
) -> str:
    """Return an exact shell-ready repair command for the given scope."""
    return shlex.join(
        build_repair_argv(
            vault_root,
            scope,
            launcher=launcher,
            json_mode=json_mode,
            dry_run=dry_run,
        )
    )


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
