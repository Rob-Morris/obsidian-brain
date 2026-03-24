#!/usr/bin/env python3
"""
read.py — Read Brain vault resources from the compiled router.

Queries the compiled router JSON for artefact types, triggers, styles,
templates, skills, plugins, memories, environment, and router metadata.
Also runs structural compliance checks.

The MCP server imports these functions directly (with its in-memory router),
avoiding JSON parsing overhead. Standalone CLI reads from disk.

Usage:
    python3 read.py artefact
    python3 read.py artefact --name wiki
    python3 read.py trigger
    python3 read.py style --name concise
    python3 read.py template --name wiki
    python3 read.py skill
    python3 read.py memory --name "brain core"
    python3 read.py environment
    python3 read.py router
    python3 read.py compliance --name error
"""

import json
import os
import sys

from _common import find_vault_root

COMPILED_ROUTER_REL = os.path.join("_Config", ".compiled-router.json")


# ---------------------------------------------------------------------------
# File reading helper
# ---------------------------------------------------------------------------

def read_file_content(vault_root, rel_path):
    """Read a file's content given a relative path from vault root.

    Resolves wikilink-style paths (no extension → try .md).
    Returns file content as string, or an error message.
    """
    abs_path = os.path.join(vault_root, rel_path)
    # Resolve wikilink-style paths (no extension → try .md)
    if not os.path.isfile(abs_path) and not rel_path.endswith(".md"):
        abs_path += ".md"
    if not os.path.isfile(abs_path):
        return f"Error: file not found: {rel_path}"
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Resource readers — each takes (router, vault_root, name) and returns a value
# ---------------------------------------------------------------------------

def read_named_resource(router, vault_root, resource_label, name, router_key, doc_field):
    """List items or read a specific item's file content by name."""
    items = router[router_key]
    if name:
        match = next((i for i in items if i["name"] == name), None)
        if not match:
            return {"error": f"No {resource_label} matching '{name}'"}
        return read_file_content(vault_root, match[doc_field])
    return items


def read_artefact(router, vault_root, name=None):
    """List artefact types, or filter by key/type name."""
    artefacts = router["artefacts"]
    if name:
        matches = [a for a in artefacts if a["key"] == name or a["type"] == name]
        if not matches:
            return {"error": f"No artefact matching '{name}'"}
        return matches
    return artefacts


def read_trigger(router, vault_root, name=None):
    """List all triggers."""
    return router["triggers"]


def read_style(router, vault_root, name=None):
    """List styles, or read a specific style file by name."""
    return read_named_resource(router, vault_root, "style", name, "styles", "style_doc")


def read_template(router, vault_root, name=None):
    """Read a template file by artefact type key."""
    if not name:
        return {"error": "template resource requires a name parameter (artefact type key)"}
    artefacts = router["artefacts"]
    match = next((a for a in artefacts if a["key"] == name or a["type"] == name), None)
    if not match:
        return {"error": f"No artefact matching '{name}'"}
    if not match.get("template_file"):
        return {"error": f"Artefact '{name}' has no template file"}
    return read_file_content(vault_root, match["template_file"])


def read_skill(router, vault_root, name=None):
    """List skills, or read a specific skill file by name."""
    return read_named_resource(router, vault_root, "skill", name, "skills", "skill_doc")


def read_plugin(router, vault_root, name=None):
    """List plugins, or read a specific plugin file by name."""
    return read_named_resource(router, vault_root, "plugin", name, "plugins", "skill_doc")


def read_memory(router, vault_root, name=None):
    """List memories, or search by trigger/name (case-insensitive substring)."""
    memories = router.get("memories", [])
    if name:
        # Case-insensitive substring search across triggers
        lower_name = name.lower()
        matches = [m for m in memories
                   if any(lower_name in t.lower() for t in m.get("triggers", []))]
        # Fallback to exact name match
        if not matches:
            matches = [m for m in memories if m["name"].lower() == lower_name]
        if not matches:
            return {"error": f"No memory matching '{name}'"}
        if len(matches) == 1:
            return read_file_content(vault_root, matches[0]["memory_doc"])
        return matches
    return memories


def read_environment(router, vault_root, name=None):
    """Return runtime environment info."""
    return dict(router["environment"])


def read_router_meta(router, vault_root, name=None):
    """Return always-rules and metadata."""
    return {
        "always_rules": router["always_rules"],
        "meta": router["meta"],
    }


def read_compliance(router, vault_root, name=None):
    """Run structural compliance checks. name = severity filter."""
    from check import run_checks
    result = run_checks(str(vault_root), router)
    if name:  # name parameter doubles as severity filter
        result["findings"] = [f for f in result["findings"] if f["severity"] == name]
        result["summary"] = {
            "errors": sum(1 for f in result["findings"] if f["severity"] == "error"),
            "warnings": sum(1 for f in result["findings"] if f["severity"] == "warning"),
            "info": sum(1 for f in result["findings"] if f["severity"] == "info"),
        }
    return result


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

RESOURCES = {
    "artefact": read_artefact,
    "trigger": read_trigger,
    "style": read_style,
    "template": read_template,
    "skill": read_skill,
    "plugin": read_plugin,
    "memory": read_memory,
    "environment": read_environment,
    "router": read_router_meta,
    "compliance": read_compliance,
}


def read_resource(router, vault_root, resource, name=None):
    """Dispatch to the appropriate resource reader.

    Returns the resource data (dict, list, or string).
    For unknown resources, returns an error dict.
    """
    handler = RESOURCES.get(resource)
    if not handler:
        return {"error": f"Unknown resource '{resource}'. Valid: {', '.join(RESOURCES)}"}
    return handler(router, vault_root, name)


# ---------------------------------------------------------------------------
# Compiled router loading (CLI only — MCP server passes its in-memory copy)
# ---------------------------------------------------------------------------

def load_compiled_router(vault_root):
    """Load the compiled router JSON from disk."""
    router_path = os.path.join(str(vault_root), COMPILED_ROUTER_REL)
    if not os.path.isfile(router_path):
        print(
            f"Error: compiled router not found at {COMPILED_ROUTER_REL}. "
            f"Run compile_router.py first.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(router_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    resource = None
    name = None
    vault_arg = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--name" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        elif not arg.startswith("--") and resource is None:
            resource = arg
            i += 1
        else:
            i += 1

    if not resource:
        print(
            f"Usage: read.py RESOURCE [--name NAME] [--vault PATH]\n"
            f"Resources: {', '.join(RESOURCES)}",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))
    router = load_compiled_router(vault_root)
    result = read_resource(router, vault_root, resource, name)

    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
