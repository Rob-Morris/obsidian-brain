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

from _common import (
    COMPILED_ROUTER_REL,
    find_vault_root,
    is_archived_path,
    load_compiled_router as _load_compiled_router,
    match_artefact,
    read_file_content,
    resolve_and_check_bounds,
    resolve_and_validate_folder,
    resolve_artefact_path,
)


def _check_vault_containment(vault_root, rel_path):
    """Return an error dict if rel_path escapes the vault root, else None."""
    try:
        resolve_and_check_bounds(os.path.join(vault_root, rel_path), vault_root)
    except ValueError:
        return {"error": "Path escapes vault root"}
    return None
# ---------------------------------------------------------------------------
# Resource readers — each takes (router, vault_root, name) and returns a value
# ---------------------------------------------------------------------------

def _require_name(resource_label, name):
    """Raise ValueError if name is missing, directing to brain_list."""
    if not name:
        raise ValueError(
            f"brain_read(resource='{resource_label}') requires name. "
            f"To list all {resource_label}s, use brain_list(resource='{resource_label}')."
        )


def read_named_resource(router, vault_root, resource_label, name, router_key, doc_field):
    """Read a specific item's file content by name.

    Requires name. Listing (name=None) is handled by brain_list.
    """
    items = router[router_key]
    _require_name(resource_label, name)
    match = next((i for i in items if i["name"] == name), None)
    if not match:
        return {"error": f"No {resource_label} matching '{name}'"}
    return read_file_content(vault_root, match[doc_field])


def read_type(router, vault_root, name=None):
    """Read a specific artefact type definition by key/name.

    Listing via brain_list(resource='type').
    """
    artefacts = router["artefacts"]
    _require_name("type", name)
    match = match_artefact(artefacts, name)
    if not match:
        return {"error": f"No artefact matching '{name}'"}
    return [match]


def read_trigger(router, vault_root, name=None):
    """Read a specific trigger by name. Listing via brain_list(resource='trigger')."""
    triggers = router["triggers"]
    _require_name("trigger", name)
    match = next((t for t in triggers if t["name"] == name), None)
    if not match:
        return {"error": f"No trigger matching '{name}'"}
    return match


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
    """Read a specific memory by trigger/name (case-insensitive substring).

    Listing via brain_list(resource='memory').
    """
    _require_name("memory", name)
    memories = router.get("memories", [])
    lower_name = name.lower()
    matches = [m for m in memories
               if any(lower_name in t.lower() for t in m.get("triggers", []))]
    if not matches:
        matches = [m for m in memories if m["name"].lower() == lower_name]
    if not matches:
        return {"error": f"No memory matching '{name}'"}
    if len(matches) == 1:
        return read_file_content(vault_root, matches[0]["memory_doc"])
    return matches


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
        if is_archived_path(name):
            return {
                "error": f"'{name}' is archived. "
                "Use brain_read(resource=\"archive\", name=\"...\") to read archived files."
            }
        return _check_vault_containment(vault_root, name) or read_file_content(vault_root, name)

    try:
        name, _ = resolve_and_validate_folder(vault_root, router, name)
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
        if is_archived_path(name):
            return {
                "error": f"'{name}' is archived. "
                "Use brain_read(resource=\"archive\", name=\"...\") to read archived files."
            }
        return _check_vault_containment(vault_root, name) or read_file_content(vault_root, name)

    # Try artefact folders first
    try:
        resolved, _ = resolve_and_validate_folder(vault_root, router, name)
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
# Archive resource
# ---------------------------------------------------------------------------

def read_archive(router, vault_root, name=None):
    """Read a specific archived file by path inside _Archive/.

    Listing via brain_list(resource='archive').
    """
    _require_name("archive", name)
    if not is_archived_path(name):
        return {"error": f"'{name}' is not in _Archive/. "
                "Use brain_read(resource=\"artefact\") for active files."}
    return read_file_content(vault_root, name)


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
    "archive": read_archive,
}


def read_resource(router, vault_root, resource, name=None):
    """Dispatch to the appropriate resource reader.

    Returns the resource data (dict, list, or string).
    For unknown resources, returns an error dict.
    Raises ValueError (via handlers) when name is required but missing.
    """
    handler = RESOURCES.get(resource)
    if not handler:
        return {"error": f"Unknown resource '{resource}'. Valid: {', '.join(RESOURCES)}"}
    return handler(router, vault_root, name)


# ---------------------------------------------------------------------------
# Compiled router loading (CLI only — MCP server passes its in-memory copy)
# ---------------------------------------------------------------------------

def load_compiled_router(vault_root):
    """Load the compiled router JSON from disk or exit with a CLI-friendly error."""
    router = _load_compiled_router(vault_root)
    if "error" in router:
        print(
            f"Error: {router['error']}",
            file=sys.stderr,
        )
        sys.exit(1)
    return router


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
