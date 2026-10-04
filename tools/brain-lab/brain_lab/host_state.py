from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from .manifests import manifest_tree
from .model import canonical_json


def _hash_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False}
    if not path.is_file():
        return {"present": True, "kind": "not-file"}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return {"present": True, "size": path.stat().st_size, "sha256": digest.hexdigest()}


def _hash_directory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False}
    entries = []
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path).as_posix()
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        row = {
            "path": relative,
            "mode": stat.S_IMODE(metadata.st_mode),
            "size": metadata.st_size,
        }
        if stat.S_ISLNK(metadata.st_mode):
            row.update({"kind": "symlink", "target": os.readlink(candidate)})
        elif stat.S_ISREG(metadata.st_mode):
            row.update({"kind": "file", **_hash_file(candidate)})
        else:
            row["kind"] = "special"
        entries.append(row)
    return {
        "present": True,
        "entry_count": len(entries),
        "sha256": hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest(),
    }


# The lab cannot import Brain Core, so this repeats `_bootstrap.mcp_state.BRAIN_SERVER_NAME`.
BRAIN_SERVER_NAME = "brain"


def _brain_server_entries(path: Path) -> dict[str, Any]:
    """Hash the projection of ``~/.claude.json`` onto the ``brain`` MCP server entries.

    Claude Code rewrites the rest of this file for its own state while any
    session runs, so a whole-file hash cannot tell a lab leak from that churn.
    A file this projection cannot read falls back to its whole-file hash.
    """
    if not path.is_file():
        return _hash_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"unreadable": True, **_hash_file(path)}
    if not isinstance(data, dict):
        return {"unreadable": True, **_hash_file(path)}

    def server_entry(holder: Any) -> Any:
        servers = holder.get("mcpServers") if isinstance(holder, dict) else None
        # A malformed server table is kept whole, so damage to it still shows.
        return servers.get(BRAIN_SERVER_NAME) if isinstance(servers, dict) else servers

    projects = data.get("projects")
    if isinstance(projects, dict):
        entries = {project: server_entry(holder) for project, holder in projects.items()}
        projects = {project: entry for project, entry in entries.items() if entry is not None}
    projection = {"user": server_entry(data), "projects": projects}
    return {"present": True, "sha256": hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()}


def _brain_cli_state(binary: str | None) -> dict[str, Any]:
    if binary is None:
        return {"present": False}
    path = Path(binary)
    try:
        completed = subprocess.run(
            [binary, "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        version = completed.stdout.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        version = f"<error:{type(exc).__name__}>"
    return {
        "present": True,
        "path": str(path),
        "link_target": os.readlink(path) if path.is_symlink() else None,
        "file": _hash_file(path),
        "version": version,
    }


def _git_state(path: Path) -> dict[str, Any]:
    def run(*arguments: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("failed to fingerprint declared Git worktree") from exc
        return result.stdout.decode("utf-8", errors="surrogateescape")

    return {
        "head": run("rev-parse", "HEAD").strip(),
        "status_sha256": hashlib.sha256(run("status", "--porcelain=v2", "--untracked-files=all").encode()).hexdigest(),
        "worktree_diff_sha256": hashlib.sha256(run("diff", "--binary").encode()).hexdigest(),
        "index_diff_sha256": hashlib.sha256(run("diff", "--cached", "--binary").encode()).hexdigest(),
    }


def _runtime_inventory(home: Path) -> list[dict[str, Any]]:
    root = home / ".brain" / "venvs"
    if not root.is_dir():
        return []
    return [
        {
            "name": child.name,
            "pyvenv": _hash_file(child / "pyvenv.cfg"),
            "dependency_marker": _hash_file(child / ".brain-deps-installed"),
            "python_link": (
                os.readlink(child / "bin" / "python")
                if (child / "bin" / "python").is_symlink()
                else None
            ),
        }
        for child in sorted(root.iterdir())
        if child.is_dir()
    ]


def capture_host_state(
    *,
    worktrees: Iterable[Path] = (),
    vaults: Iterable[Path] = (),
    home: Path | None = None,
) -> dict[str, Any]:
    home = (home or Path.home()).expanduser().resolve()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser().resolve()
    brain_binary = shutil.which("brain")
    files = {
        "brain_machine_config": _hash_directory(config_home / "brain"),
        "claude_global": _brain_server_entries(home / ".claude.json"),
        "codex_global": _hash_file(home / ".codex" / "config.toml"),
        "brain_cli": _brain_cli_state(brain_binary),
    }
    workspace_states = {}
    for path in worktrees:
        resolved = path.expanduser().resolve()
        workspace_states[str(resolved)] = _git_state(resolved)
    vault_states = {}
    for path in vaults:
        resolved = path.expanduser().resolve()
        vault_states[str(resolved)] = manifest_tree(resolved).tree_sha256
    payload = {
        "files": files,
        "managed_runtimes": _runtime_inventory(home),
        "worktrees": workspace_states,
        "vaults": vault_states,
    }
    payload["fingerprint"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return payload
