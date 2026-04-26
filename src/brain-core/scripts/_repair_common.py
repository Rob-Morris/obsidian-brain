#!/usr/bin/env python3
"""Shared repair metadata and launcher-safe helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import functools
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
import shlex


DEFAULT_REPAIR_LAUNCHER = "python3.12"
MANAGED_RUNTIME_ENV = "BRAIN_REPAIR_MANAGED"
BOOTSTRAP_SUMMARY_ENV = "BRAIN_REPAIR_BOOTSTRAP_SUMMARY"
VENV_PYTHON_REL = Path(".venv/bin/python")
REQUIREMENTS_REL = Path(".brain-core/brain_mcp/requirements.txt")
REPAIR_SCRIPT_REL = Path(".brain-core/scripts/repair.py")
REPAIR_SCOPE_MODULES = {
    "mcp": ("mcp", "yaml"),
    "router": (),
    "index": (),
    "registry": (),
}

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
}


def iso_now() -> str:
    """Return the current local timestamp in ISO 8601 form."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def step(name: str, status: str, message: str, **extra: Any) -> dict:
    """Build a structured repair step record."""
    payload = {"name": name, "status": status, "message": message}
    payload.update(extra)
    return payload


def make_result_envelope(
    *,
    scope: str,
    vault_root: str | Path,
    dry_run: bool,
    managed_python: str,
    steps: list[dict],
    status: str,
    notes: list[str] | None = None,
) -> dict:
    """Build the canonical repair-result envelope shared across runtime and bootstrap paths."""
    result = {
        "scope": scope,
        "vault_root": str(vault_root),
        "checked_at": iso_now(),
        "dry_run": dry_run,
        "managed_python": managed_python,
        "status": status,
        "steps": steps,
    }
    if notes:
        result["notes"] = notes
    return result


def required_modules_for_scope(scope: str) -> tuple[str, ...]:
    """Return third-party modules required by the named repair scope."""
    return REPAIR_SCOPE_MODULES.get(scope, ())


def probe_python(python_path: str, *, modules: tuple[str, ...] = ()) -> dict:
    code = (
        "import importlib.util, json, sys; "
        f"mods = {modules!r}; "
        "missing = [name for name in mods if importlib.util.find_spec(name) is None]; "
        "payload = {"
        "  'major': sys.version_info[0],"
        "  'minor': sys.version_info[1],"
        "  'missing': missing,"
        "}; "
        "payload['compatible'] = (payload['major'], payload['minor']) >= (3, 12); "
        "payload['ok'] = payload['compatible'] and not missing; "
        "print(json.dumps(payload))"
    )
    try:
        result = subprocess.run(
            [python_path, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "compatible": False, "missing": list(modules)}
    if result.returncode != 0:
        return {"ok": False, "compatible": False, "missing": list(modules)}
    try:
        return json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "compatible": False, "missing": list(modules)}


def is_compatible_python(python_path: str) -> bool:
    """Return True when the path is a usable Python 3.12+ launcher."""
    return bool(probe_python(python_path).get("compatible"))


@functools.lru_cache(maxsize=1)
def find_repair_launcher() -> str | None:
    """Return the best available Python launcher for repair guidance."""
    candidates = [sys.executable]
    for name in ("python3.13", "python3.12", "python3"):
        path = shutil.which(name)
        if path and path not in candidates:
            candidates.append(path)

    for candidate in candidates:
        if candidate and is_compatible_python(candidate):
            return candidate
    return None


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
    launcher = launcher or find_repair_launcher() or DEFAULT_REPAIR_LAUNCHER
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
