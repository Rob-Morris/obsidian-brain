#!/usr/bin/env python3
"""Read the active Brain identity needed by a host fixture.

This script is streamed to a retained run over ``docker exec -i``.  It must stay
read-only: imports disable bytecode writes and every result is emitted on
stdout rather than persisted in the container.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tomllib


ALLOWED_MCP_ENVIRONMENT = {
    "BRAIN_VAULT_ROOT",
    "BRAIN_WORKSPACE_DIR",
    "PYTHONPATH",
}


def _core_identity(vault: Path, helper: Path) -> dict[str, object]:
    if helper.is_symlink() or not helper.is_file():
        raise RuntimeError(f"Brain Lab Core manifest helper is missing or unsafe: {helper}")
    specification = importlib.util.spec_from_file_location("brain_lab_tree_manifest", helper)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Brain Lab Core manifest helper cannot be loaded: {helper}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    identity = module.manifest(vault, "core")
    return {
        "tree_sha256": identity["tree_sha256"],
        "file_count": len(identity["entries"]),
        "total_bytes": identity["total_bytes"],
    }


def _regular_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"MCP configuration is not a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"MCP configuration root is not an object: {path}")
    return value


def _brain_mcp_config(vault: Path) -> tuple[dict, str]:
    claude_path = vault / ".mcp.json"
    claude = _regular_json(claude_path)
    if claude is not None:
        servers = claude.get("mcpServers")
        if isinstance(servers, dict) and "brain" in servers:
            return servers["brain"], claude_path.relative_to(vault).as_posix()

    codex_path = vault / ".codex" / "config.toml"
    if codex_path.exists():
        if (vault / ".codex").is_symlink():
            raise RuntimeError("Codex configuration directory is symlinked")
        if codex_path.is_symlink() or not codex_path.is_file():
            raise RuntimeError(f"MCP configuration is not a regular file: {codex_path}")
        value = tomllib.loads(codex_path.read_text(encoding="utf-8"))
        servers = value.get("mcp_servers")
        if isinstance(servers, dict) and "brain" in servers:
            return servers["brain"], codex_path.relative_to(vault).as_posix()

    raise RuntimeError("active Brain has no project Brain MCP entry")


def _validated_mcp_config(vault: Path, core: Path) -> dict[str, object]:
    server, source = _brain_mcp_config(vault)
    if not isinstance(server, dict):
        raise RuntimeError("Brain MCP entry is not an object")
    command = server.get("command")
    args = server.get("args")
    environment = server.get("env", {})
    if not isinstance(command, str) or not command:
        raise RuntimeError("Brain MCP entry has no command")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise RuntimeError("Brain MCP entry arguments are invalid")
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise RuntimeError("Brain MCP entry environment is invalid")
    unexpected = sorted(set(environment) - ALLOWED_MCP_ENVIRONMENT)
    if unexpected:
        raise RuntimeError(
            "Brain MCP entry contains environment fields that cannot be exported: "
            + ", ".join(unexpected)
        )
    if args != ["-m", "brain_mcp.proxy", command, "brain_mcp.server"]:
        raise RuntimeError("Brain MCP entry does not use the expected proxy/server modules")
    command_path = Path(command)
    if not command_path.is_absolute() or not command_path.is_file() or not os.access(command, os.X_OK):
        raise RuntimeError("Brain MCP command is not an executable absolute container path")
    if environment.get("PYTHONPATH") != str(core):
        raise RuntimeError("Brain MCP PYTHONPATH does not select the active Brain Core")
    for key in ("BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR"):
        value = environment.get(key)
        if value is not None and value != str(vault):
            raise RuntimeError(f"Brain MCP {key} does not select the active Brain vault")
    return {
        "source": source,
        "vault_path": str(vault),
        "command": command,
        "args": args,
        "environment": dict(sorted(environment.items())),
    }


def _skill_identity(vault: Path, name: str) -> dict[str, object]:
    core = vault / ".brain-core"
    for directory in (
        core / "scripts",
        core / "scripts" / "_bootstrap",
        core / "scripts" / "_portable",
        core / "scripts" / "_skill_library",
        core / "skills",
        core / "client-adapters",
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeError(f"active Brain fixture dependency is missing or unsafe: {directory}")
    sys.path.insert(0, str(core / "scripts"))
    from _bootstrap.agent_skills import load_effective_skill_adapter
    from _portable.skill_resolution import effective_skill_path
    from _skill_library.packages import inspect_package, validate_skill_name

    validate_skill_name(name)
    user_package = vault / "_Config" / "Skills" / name
    core_package = core / "skills" / name
    if user_package.exists() or user_package.is_symlink():
        for directory in (vault / "_Config", vault / "_Config" / "Skills"):
            if directory.is_symlink() or not directory.is_dir():
                raise RuntimeError(f"user skill parent is missing or unsafe: {directory}")
    if user_package.is_symlink() or core_package.is_symlink():
        raise RuntimeError(f"Brain skill package is symlinked: {name}")
    package = effective_skill_path(user_package, core_package)
    source = "user" if package == user_package else "core"
    snapshot = inspect_package(package, expected_name=name)
    adapter = load_effective_skill_adapter(vault, name)
    adapter_sha256 = hashlib.sha256(adapter.encode("utf-8")).hexdigest()
    return {
        "name": name,
        "source": source,
        "package_path": package.relative_to(vault).as_posix(),
        "package_tree_sha256": snapshot.package_sha256,
        "package_files": [
            {
                "path": item.path,
                "sha256": item.sha256,
                "size": item.size,
                "executable": item.executable,
            }
            for item in snapshot.files
        ],
        "adapter": adapter,
        "adapter_sha256": adapter_sha256,
    }


def inspect(vault: Path, skill_names: list[str], tree_manifest_helper: Path) -> dict[str, object]:
    if vault.is_symlink() or not vault.is_dir():
        raise RuntimeError("active Brain vault must be a real directory")
    vault = vault.resolve()
    core = vault / ".brain-core"
    version_path = core / "VERSION"
    if core.is_symlink() or not core.is_dir() or version_path.is_symlink():
        raise RuntimeError("active Brain Core and VERSION must not be symlinks")
    if not version_path.is_file():
        raise RuntimeError("selected run does not contain an installed Brain")
    return {
        "schema": "brain-lab.host-fixture-probe/1",
        "vault_path": str(vault),
        "core": {
            "version": version_path.read_text(encoding="utf-8").strip(),
            **_core_identity(vault, tree_manifest_helper),
        },
        "mcp": _validated_mcp_config(vault, core),
        "skills": [_skill_identity(vault, name) for name in skill_names],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--skill", action="append", dest="skills", required=True)
    parser.add_argument(
        "--tree-manifest",
        type=Path,
        default=Path("/usr/local/lib/brain-lab/tree_manifest.py"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            inspect(args.vault, args.skills, args.tree_manifest),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
