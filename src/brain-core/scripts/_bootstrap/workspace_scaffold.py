#!/usr/bin/env python3
"""Launcher-safe workspace-local scaffold helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from _common import safe_write


DEFAULT_BRAIN_IGNORE_ENTRIES = (".brain/local/",)
CLAUDE_LOCAL_SETTINGS_IGNORE = ".claude/settings.local.json"
CLAUDE_LOCAL_MD_IGNORE = ".claude/CLAUDE.local.md"
CODEX_PROJECT_IGNORE_ENTRIES = (".codex/config.toml",)


class GitInspectionError(RuntimeError):
    """Raised when git metadata cannot be inspected reliably."""


def _run_git_rev_parse(target_dir: Path, *args: str, error_label: str) -> Optional[Path]:
    if shutil.which("git") is None:
        return None
    if not (target_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(target_dir), "rev-parse", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitInspectionError(f"failed to inspect {error_label}: {exc}") from exc
    if result.returncode != 0:
        raise GitInspectionError(result.stderr.strip() or f"git rev-parse {' '.join(args)} failed")
    resolved = result.stdout.strip()
    return Path(resolved).resolve() if resolved else None


def _git_repo_root(target_dir: Path) -> Optional[Path]:
    return _run_git_rev_parse(
        target_dir,
        "--show-toplevel",
        error_label="git repository root",
    )


def _git_dir(target_dir: Path) -> Optional[Path]:
    return _run_git_rev_parse(
        target_dir,
        "--path-format=absolute",
        "--git-dir",
        error_label="git directory",
    )


def _brain_ignore_entries(
    scope: str,
    clients: List[str],
    *,
    skip_mcp: bool,
) -> List[str]:
    entries = list(DEFAULT_BRAIN_IGNORE_ENTRIES)
    if skip_mcp:
        return entries
    if "claude" in clients:
        entries.append(CLAUDE_LOCAL_SETTINGS_IGNORE)
        if scope == "local":
            entries.append(CLAUDE_LOCAL_MD_IGNORE)
    if "codex" in clients and scope == "project":
        entries.extend(CODEX_PROJECT_IGNORE_ENTRIES)
    return entries


def ensure_brain_ignore_rules(
    target_dir: Path,
    scope: str,
    clients: List[str],
    *,
    skip_mcp: bool,
) -> str | None:
    """Ensure git ignore rules for Brain-owned local state when target is a repo root."""
    repo_root = _git_repo_root(target_dir)
    if repo_root is None or repo_root != target_dir.resolve():
        return None

    gitignore_path = target_dir / ".gitignore"
    destination = gitignore_path if gitignore_path.exists() else None
    if destination is None:
        git_dir = _git_dir(target_dir)
        if git_dir is None:
            return None
        destination = git_dir / "info" / "exclude"

    entries = _brain_ignore_entries(scope, clients, skip_mcp=skip_mcp)
    try:
        existing = destination.read_text(encoding="utf-8") if destination.is_file() else ""
    except OSError as exc:
        raise GitInspectionError(
            f"failed to read ignore destination {destination}: {exc}"
        ) from exc

    existing_entries = {line.strip() for line in existing.splitlines() if line.strip()}
    missing = [entry for entry in entries if entry not in existing_entries]
    if not missing:
        rendered = destination.relative_to(target_dir) if destination.is_relative_to(target_dir) else destination
        return f"{rendered} already covers Brain local state"

    prefix = ""
    if existing and not existing.endswith("\n"):
        prefix += "\n"
    if existing_entries:
        prefix += "\n"

    comment = "# Brain local state\n"
    if "# Brain local state" in existing_entries:
        comment = ""

    block = prefix + comment + "".join(f"{entry}\n" for entry in missing)
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_write(destination, existing + block)
    return f"Updated {destination}"
