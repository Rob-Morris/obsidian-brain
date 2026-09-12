"""Native Grok registration and exact ownership of its Brain startup rule."""

from __future__ import annotations

from pathlib import Path
import tomllib
from copy import deepcopy

from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _bootstrap.mcp_state import (
    GROK_CONFIG_REL,
    GROK_RULE_REL,
    _parse_toml_sections,
    _render_toml,
    _toml_body_lines,
    _upsert_toml_section,
    render_toml_config,
    render_toml_without_server,
)

RULE_CONTENT = (
    "<!-- Managed by Obsidian Brain: Grok session bootstrap -->\n"
    "ALWAYS DO FIRST: Call the Brain MCP `session_start` tool and follow its "
    "returned bootstrap and workspace context. If MCP is unavailable, run "
    "`brain session start --json` from this workspace.\n"
)


def plan_rule(plan: FilePlan, root: Path, *, remove: bool = False) -> Path:
    """Refuse collisions on install; remove only the exact authored rule."""
    path = root / GROK_RULE_REL
    content = plan.read_text(path)
    if remove:
        if content == RULE_CONTENT:
            plan.delete(path)
    else:
        if content is not None and content != RULE_CONTENT:
            raise ValueError(f"Grok Brain rule has custom content; preserved: {path}")
        plan.write_text(path, RULE_CONTENT)
    return path


def configure_rule(root: Path, *, remove: bool = False) -> bool:
    """Apply the same owned rule for standalone bootstrap and MCP setup."""
    plan = FilePlan()
    plan_rule(plan, root, remove=remove)
    changes = plan.changes()
    apply_file_changes(changes)
    return bool(changes)


def read_server(content: str) -> dict | None:
    """Read full TOML state, including user-added options used for ownership checks."""
    servers = tomllib.loads(content).get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Grok mcp_servers must be a table")
    server = servers.get("brain")
    if server is not None and not isinstance(server, dict):
        raise ValueError("Grok mcp_servers.brain must be a table")
    return server


def render_config(content: str, server: dict) -> str:
    """Replace transport fields while preserving user options and other servers."""
    existing = read_server(content) or {}
    expected = {**existing, **server}
    # A configured remote transport must not silently become a stdio transport.
    if any(key in existing for key in ("url", "headers", "http_headers")):
        raise ValueError(
            "Grok's existing brain server uses a remote transport; preserved"
        )
    rendered = render_toml_config(content, server)
    preamble, sections = _parse_toml_sections(rendered)
    main = {
        key: value for key, value in expected.items() if not isinstance(value, dict)
    }
    _upsert_toml_section(sections, "mcp_servers.brain", _toml_body_lines(main))
    rendered = _render_toml(preamble, sections)
    expected_document = deepcopy(tomllib.loads(content))
    expected_document.setdefault("mcp_servers", {})["brain"] = expected
    if tomllib.loads(rendered) != expected_document:
        raise ValueError(
            "Grok Brain TOML layout cannot be updated without losing custom settings"
        )
    return rendered


def plan_configure(plan: FilePlan, root: Path, server: dict) -> tuple[Path, Path]:
    """Preflight both files before either registration or instructions can change."""
    rule = plan_rule(plan, root)
    config = root / GROK_CONFIG_REL
    plan.write_text(config, render_config(plan.read_text(config) or "", server))
    return config, rule


def plan_remove(plan: FilePlan, root: Path, server: dict) -> bool:
    """Remove exact registration and rule, preserving user-modified Brain entries."""
    config = root / GROK_CONFIG_REL
    content = plan.read_text(config)
    if content is not None:
        current = read_server(content)
        if current is not None and current != server:
            return False
        if current is not None:
            rendered = render_toml_without_server(content, server)
            if rendered is None:
                return False
            expected_document = deepcopy(tomllib.loads(content))
            del expected_document["mcp_servers"]["brain"]
            if not expected_document["mcp_servers"]:
                del expected_document["mcp_servers"]
            actual_document = tomllib.loads(rendered)
            if actual_document.get("mcp_servers") == {}:
                del actual_document["mcp_servers"]
            if actual_document != expected_document:
                raise ValueError("Grok removal would change unrelated TOML settings")
            if rendered:
                plan.write_text(config, rendered)
            else:
                plan.delete(config)
    plan_rule(plan, root, remove=True)
    # Retain the ownership record if a user has edited the rule.
    return plan.read_text(root / GROK_RULE_REL) is None
