#!/usr/bin/env python3
"""
session.py — Compile a bootstrap session payload for agent use.

Assembles a token-efficient payload from the compiled router plus user
preference files.  The MCP server calls compile_session() with its
in-memory router; the standalone CLI reads the router from disk.

Usage:
    python3 session.py
    python3 session.py --vault /path/to/vault --json
    python3 session.py --context mcp-spike
"""

import json
import os
import sys

from _common import find_vault_root, parse_frontmatter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREFERENCES_REL = os.path.join("_Config", "User", "preferences-always.md")
GOTCHAS_REL = os.path.join("_Config", "User", "gotchas.md")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_user_body(vault_root, rel_path):
    """Read a user file and return its body with frontmatter stripped.

    Returns "" if the file does not exist or is empty.
    """
    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    if not text.strip():
        return ""
    _, body = parse_frontmatter(text)
    return body.strip()


def _condense_artefacts(artefacts):
    """Extract the fields agents need from full artefact entries."""
    condensed = []
    for a in artefacts:
        naming = a.get("naming") or {}
        fm = a.get("frontmatter") or {}
        condensed.append({
            "type": a.get("type"),
            "key": a.get("key"),
            "path": a.get("path"),
            "naming_pattern": naming.get("pattern"),
            "status_enum": fm.get("status_enum"),
            "configured": a.get("configured", False),
        })
    return condensed


def _condense_memories(memories):
    """Extract name and triggers only."""
    return [{"name": m["name"], "triggers": m.get("triggers", [])} for m in memories]


def _condense_skills(skills):
    """Extract name and source only."""
    return [{"name": s["name"], "source": s.get("source", "user")} for s in skills]


def _condense_plugins(plugins):
    """Extract name only."""
    return [{"name": p["name"]} for p in plugins]


def _extract_style_names(styles):
    """Extract just the style names."""
    return [s["name"] for s in styles]


# ---------------------------------------------------------------------------
# Main compilation
# ---------------------------------------------------------------------------


def compile_session(router, vault_root, obsidian_cli_available=False, context=None, config=None):
    """Compile a bootstrap session payload from router + user files.

    Args:
        router: The compiled router dict (in-memory or loaded from JSON).
        vault_root: Absolute path to the vault root.
        obsidian_cli_available: Whether the Obsidian REST CLI is reachable.
        context: Optional context slug for scoped sessions (not yet implemented).
        config: Optional merged config dict from config.load_config().

    Returns:
        dict with the compiled session payload.
    """
    meta = router.get("meta", {})
    env = dict(router.get("environment", {}))
    env["obsidian_cli_available"] = obsidian_cli_available

    payload = {
        "version": "1",
        "brain_core_version": meta.get("brain_core_version", ""),
        "compiled_at": meta.get("compiled_at", ""),
        "always_rules": router.get("always_rules", []),
        "preferences": _read_user_body(vault_root, PREFERENCES_REL),
        "gotchas": _read_user_body(vault_root, GOTCHAS_REL),
        "triggers": router.get("triggers", []),
        "artefacts": _condense_artefacts(router.get("artefacts", [])),
        "environment": env,
        "memories": _condense_memories(router.get("memories", [])),
        "skills": _condense_skills(router.get("skills", [])),
        "plugins": _condense_plugins(router.get("plugins", [])),
        "styles": _extract_style_names(router.get("styles", [])),
    }

    if config:
        vault_cfg = config.get("vault", {})
        defaults_cfg = config.get("defaults", {})
        profiles = list(vault_cfg.get("profiles", {}).keys())
        payload["config"] = {
            "brain_name": vault_cfg.get("brain_name", ""),
            "default_profile": defaults_cfg.get("default_profile", "operator"),
            "profiles": profiles,
        }

    if context is not None:
        payload["context"] = {
            "slug": context,
            "status": "not_implemented",
            "message": (
                "Context scoping is not yet implemented. "
                "The general bootstrap payload has been returned. "
                "Context-aware sessions will be available in a future version."
            ),
        }

    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compile session bootstrap payload")
    parser.add_argument("--vault", help="Vault root (auto-detected if omitted)")
    parser.add_argument("--context", help="Optional context slug")
    parser.add_argument("--json", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    vault_root = args.vault or find_vault_root()
    if not vault_root:
        print("Error: could not find vault root", file=sys.stderr)
        sys.exit(1)

    # Load compiled router from disk
    router_path = os.path.join(vault_root, ".brain", "local", "compiled-router.json")
    if not os.path.isfile(router_path):
        print("Error: compiled router not found — run compile_router.py first", file=sys.stderr)
        sys.exit(1)

    with open(router_path, encoding="utf-8") as f:
        router = json.load(f)

    result = compile_session(router, vault_root, context=args.context)

    indent = 2 if args.json else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))


if __name__ == "__main__":
    main()
