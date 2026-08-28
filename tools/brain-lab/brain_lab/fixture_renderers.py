from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


Renderer = Callable[[Path, Path], tuple[str, dict[str, str]]]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_codex(root: Path, bridge: Path) -> tuple[str, dict[str, str]]:
    codex_home = root / "clients" / "codex" / "home"
    codex_home.mkdir(parents=True)
    (codex_home / "skills").symlink_to(
        Path("../../../shared/skills"),
        target_is_directory=True,
    )
    _write_text(
        codex_home / "config.toml",
        "[mcp_servers.brain]\n"
        f"command = {_toml_string(str(bridge))}\n"
        "args = []\n",
    )
    _write_json(
        root / "clients" / "codex" / "environment.json",
        {"CODEX_HOME": str(codex_home)},
    )
    return "codex", {
        "home": "clients/codex/home",
        "config": "clients/codex/home/config.toml",
        "skills": "clients/codex/home/skills",
        "environment": "clients/codex/environment.json",
    }


def render_claude(root: Path, bridge: Path) -> tuple[str, dict[str, str]]:
    claude_project = root / "clients" / "claude" / "project"
    claude_skills_parent = claude_project / ".claude"
    claude_skills_parent.mkdir(parents=True)
    (claude_skills_parent / "skills").symlink_to(
        Path("../../../../shared/skills"),
        target_is_directory=True,
    )
    _write_json(
        claude_project / ".mcp.json",
        {
            "mcpServers": {
                "brain": {"command": str(bridge), "args": [], "env": {}}
            }
        },
    )
    _write_json(
        root / "clients" / "claude" / "environment.json",
        {"project_directory": str(claude_project)},
    )
    return "claude", {
        "project": "clients/claude/project",
        "config": "clients/claude/project/.mcp.json",
        "skills": "clients/claude/project/.claude/skills",
        "environment": "clients/claude/environment.json",
    }


RENDERERS: tuple[Renderer, ...] = (render_codex, render_claude)


def render_clients(root: Path, bridge: Path) -> dict[str, dict[str, str]]:
    return dict(renderer(root, bridge) for renderer in RENDERERS)
