#!/usr/bin/env python3
"""
read.py — Read Brain vault resources from the compiled router.

Queries the compiled router JSON for artefact types, triggers, styles,
templates, skills, plugins, memories, environment, and router metadata.
Also runs structural compliance checks.

The MCP server imports these functions directly (with its in-memory router),
avoiding JSON parsing overhead. Standalone CLI reads from disk.

Usage:
    python3 read.py type
    python3 read.py type --name wiki
    python3 read.py trigger
    python3 read.py style --name concise
    python3 read.py template --name wiki
    python3 read.py skill
    python3 read.py memory --name "brain core"
    python3 read.py environment
    python3 read.py router
    python3 read.py compliance --name error
    python3 read.py artefact --name "Designs/brain-master-design.md"
    python3 read.py file --name "obsidian-brain-dev"
"""

import json
import os
import sys

from _common import find_vault_root, match_artefact, resolve_artefact_path

COMPILED_ROUTER_REL = os.path.join(".brain", "local", "compiled-router.json")


# ---------------------------------------------------------------------------
# File reading helpers
# ---------------------------------------------------------------------------

def _check_vault_containment(vault_root, rel_path):
    """Return an error dict if rel_path escapes the vault root, else None."""
    abs_path = os.path.realpath(os.path.join(vault_root, rel_path))
    if not abs_path.startswith(os.path.realpath(vault_root) + os.sep):
        return {"error": "Path escapes vault root"}
    return None


def read_file_content(vault_root, rel_path):
    """Read a vault file's content given a relative path from vault root.

    All vault content files are ``.md``; the extension is normalised if missing.
    Falls back to the original path if the normalised path doesn't exist.
    Returns file content as string, or an error message.
    """
    original = rel_path
    if not rel_path.endswith(".md"):
        rel_path += ".md"
    abs_path = os.path.join(vault_root, rel_path)
    if not os.path.isfile(abs_path) and original != rel_path:
        abs_path = os.path.join(vault_root, original)
        rel_path = original
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


def read_type(router, vault_root, name=None):
    """List artefact types, or filter by key/type name."""
    artefacts = router["artefacts"]
    if name:
        match = match_artefact(artefacts, name)
        if not match:
            return {"error": f"No artefact matching '{name}'"}
        matches = [match]
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
    match = match_artefact(artefacts, name)
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


def read_artefact(router, vault_root, name=None):
    """Read an artefact file by relative path or basename.

    Full relative paths (containing '/') read any file in the vault directly.
    Bare basenames are resolved via wikilink-style lookup and validated against
    artefact folders.
    """
    if not name:
        return {"error": "artefact resource requires a name parameter (relative path or basename)"}

    if "/" in name:
        return _check_vault_containment(vault_root, name) or read_file_content(vault_root, name)

    try:
        from check import resolve_and_validate_folder
        name = resolve_and_validate_folder(vault_root, router, name)
    except ValueError as e:
        resource = _resolve_config_resource(vault_root, name)
        if resource:
            return {
                "error": f"'{name}' is in _Config/, not an artefact folder. "
                f"Use brain_read(resource=\"{resource}\") instead."
            }
        return {"error": str(e)}
    return read_file_content(vault_root, name)


# Mapping from _Config/ subfolder to the correct brain_read resource.
_CONFIG_RESOURCE_MAP = {
    "Memories": "memory",
    "Skills": "skill",
    "Styles": "style",
    "Templates": "template",
    "Plugins": "plugin",
}


def _resolve_config_resource(vault_root, name, file_index=None):
    """If a basename resolves to a _Config/ file, return the resource key."""
    try:
        resolved = resolve_artefact_path(name, vault_root, file_index=file_index)
    except ValueError:
        return None
    if not resolved.startswith("_Config/"):
        return None
    parts = resolved.split("/")
    if len(parts) >= 2:
        return _CONFIG_RESOURCE_MAP.get(parts[1])
    return None


def read_file(router, vault_root, name=None):
    """Read any vault file by name, delegating to the correct resource handler.

    Resolves the name and routes to the appropriate handler (artefact, memory,
    skill, etc.) so the caller doesn't need to know the resource type.
    """
    if not name:
        return {"error": "file resource requires a name parameter"}

    if "/" in name:
        return _check_vault_containment(vault_root, name) or read_file_content(vault_root, name)

    # Try artefact folders first
    try:
        from check import resolve_and_validate_folder
        resolved = resolve_and_validate_folder(vault_root, router, name)
        return read_file_content(vault_root, resolved)
    except ValueError:
        pass

    # Try _Config/ resources (memory, skill, style, etc.)
    resource = _resolve_config_resource(vault_root, name)
    if resource:
        handler = RESOURCES.get(resource)
        if handler:
            return handler(router, vault_root, name)

    return {"error": f"No vault file found matching '{name}'"}


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
    "type": read_type,
    "trigger": read_trigger,
    "style": read_style,
    "template": read_template,
    "skill": read_skill,
    "plugin": read_plugin,
    "memory": read_memory,
    "environment": read_environment,
    "router": read_router_meta,
    "compliance": read_compliance,
    "artefact": read_artefact,
    "file": read_file,
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
