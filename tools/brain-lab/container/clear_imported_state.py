#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


def _contained(vault: Path, path: Path, *, label: str) -> None:
    try:
        path.resolve(strict=False).relative_to(vault)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the vault: {path}") from exc


def _remove_directory(vault: Path, relative: str) -> bool:
    path = vault / relative
    _contained(vault, path, label="machine-state root")
    if path.is_symlink():
        raise ValueError(f"refusing to clear symlinked machine-state root: {path}")
    removed = path.is_dir()
    if removed:
        shutil.rmtree(path)
    elif path.exists():
        raise ValueError(f"machine-state root is not a directory: {path}")
    return removed


def _strip_brain_mcp_server(vault: Path) -> str:
    path = vault / ".mcp.json"
    _contained(vault, path, label="MCP configuration")
    if not path.exists():
        return "absent"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"MCP configuration is not a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot safely read MCP configuration: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"MCP configuration root is not an object: {path}")
    servers = payload.get("mcpServers")
    if servers is None:
        return "unchanged"
    if not isinstance(servers, dict):
        raise ValueError(f"MCP configuration mcpServers is not an object: {path}")
    if "brain" not in servers:
        return "unchanged"
    del servers["brain"]
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return "brain-server-removed"


def clear_imported_state(vault: Path) -> dict[str, object]:
    vault = vault.resolve()
    if not vault.is_dir():
        raise ValueError(f"vault root is not a directory: {vault}")
    removed = {
        relative: _remove_directory(vault, relative)
        for relative in (".brain/local", ".codex", ".claude")
    }
    return {"removed": removed, "mcp_json": _strip_brain_mcp_server(vault)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(clear_imported_state(args.vault), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
