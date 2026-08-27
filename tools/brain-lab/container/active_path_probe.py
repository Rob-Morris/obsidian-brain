#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


HOST_MARKERS = ("/Users/", "/private/var/", "/private/tmp/", "C:\\Users\\")
FILES = (
    Path("/home/brain/.local/bin/brain"),
    Path("/home/brain/.claude.json"),
    Path("/home/brain/.codex/config.toml"),
    Path("/home/brain/.config/brain/vaults"),
    Path("/home/brain/.config/brain/default"),
    Path("/home/brain/.config/brain/brains.json"),
)
MCP_CONFIG = Path("/home/brain/vault/.mcp.json")


def active_files() -> list[Path]:
    values = list(FILES)
    generated = Path("/home/brain/vault/.brain/local")
    if generated.is_dir():
        values.extend(path for path in generated.rglob("*") if path.is_file() or path.is_symlink())
    return sorted(set(values))


def inspect_brain_mcp(path: Path) -> list[dict]:
    if not os.path.lexists(path):
        return []
    if path.is_symlink():
        target = os.readlink(path)
        markers = [marker for marker in HOST_MARKERS if marker in target]
        return [{"path": str(path), "kind": "symlink", "markers": markers}] if markers else []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"path": str(path), "kind": "unreadable-brain-mcp", "value": str(exc)}]
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    brain = servers.get("brain") if isinstance(servers, dict) else None
    if brain is None:
        return []
    rendered = json.dumps(brain, sort_keys=True)
    markers = [marker for marker in HOST_MARKERS if marker in rendered]
    return [
        {
            "path": str(path),
            "kind": "brain-mcp-content",
            "scope": "mcpServers.brain",
            "markers": markers,
        }
    ] if markers else []


def main() -> int:
    violations = []
    checked = 0
    for path in active_files():
        if not os.path.lexists(path):
            continue
        checked += 1
        if path.is_symlink():
            target = os.readlink(path)
            if any(marker in target for marker in HOST_MARKERS):
                violations.append({"path": str(path), "kind": "symlink", "value": target})
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            violations.append({"path": str(path), "kind": "unreadable", "value": str(exc)})
            continue
        if b"\0" in content:
            continue
        text = content.decode("utf-8", errors="replace")
        markers = [marker for marker in HOST_MARKERS if marker in text]
        if markers:
            violations.append({"path": str(path), "kind": "content", "markers": markers})
    if os.path.lexists(MCP_CONFIG):
        checked += 1
        violations.extend(inspect_brain_mcp(MCP_CONFIG))
    print(json.dumps({"checked": checked, "safe": not violations, "violations": violations}, sort_keys=True))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
