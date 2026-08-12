#!/usr/bin/env python3
"""Internal compiled-router and vault resource readers.

Queries the compiled router JSON for artefact types, triggers, styles,
templates, skills, plugins, memories, environment, and router metadata.

Application owners import these functions where the portable typed owners do
not already provide the lower seam. Public callers use granular read/list
commands through ``command.py``; the parser retained here is an internal
maintenance and repository-test entry point.
"""

import argparse
import json
import os
import sys

from _common import (
    COMPILED_ROUTER_REL,
    find_vault_root,
    is_archived_path,
    load_compiled_router as _load_compiled_router,
    MissingFileResult,
    normalize_artefact_key,
    read_file_content,
    resolve_artefact_key_entry,
    resolve_and_check_bounds,
    resolve_and_validate_folder,
    resolve_artefact_path,
)
from _portable.artefact_read import read_artefact as _portable_read_artefact
from _portable.named_documents import read_named_document as _portable_read_named_document
from _portable.router_views import (
    read_environment as _portable_read_environment,
    read_router_meta as _portable_read_router_meta,
)
from _portable.router_collections import read_trigger_exact as _portable_read_trigger
from _portable.type_definitions import (
    read_template_compatible as _portable_read_template,
    read_type_compatible as _portable_read_type,
)
from _portable.vault_files import read_archived_artefact as _portable_read_archive


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
    """Raise ValueError if a named-resource read omits its reference."""
    if not name:
        raise ValueError(
            f"{resource_label}.read requires a reference. "
            "To enumerate named resources, use resource.list."
        )


def read_named_resource(router, vault_root, resource_label, name, router_key, doc_field):
    """Read a specific item's file content by name.

    Requires a name. Enumeration is owned by the resource's list command.
    """
    _require_name(resource_label, name)
    if resource_label in {"skill", "style", "plugin"}:
        return _portable_read_named_document(router, vault_root, resource_label, name)
    items = router[router_key]
    match = next((i for i in items if i["name"] == name), None)
    if not match:
        return {"error": f"No {resource_label} matching '{name}'"}
    return read_file_content(vault_root, match[doc_field])


def read_type(router, vault_root, name=None):
    """Read a specific artefact type definition by key/name.

    Enumeration is owned publicly by ``resource.list``.
    """
    _require_name("type", name)
    return _portable_read_type(router, name)


def read_trigger(router, vault_root, name=None):
    """Read a specific trigger by exact condition."""
    _require_name("trigger", name)
    return _portable_read_trigger(router, name)


def read_style(router, vault_root, name=None):
    """List styles, or read a specific style file by name."""
    return read_named_resource(router, vault_root, "style", name, "styles", "style_doc")


def read_template(router, vault_root, name=None):
    """Read a template file by artefact type key."""
    if not name:
        return {"error": "template resource requires a name parameter (artefact type key)"}
    return _portable_read_template(router, vault_root, name)


def read_skill(router, vault_root, name=None):
    """List skills, or read a specific skill file by name."""
    return read_named_resource(router, vault_root, "skill", name, "skills", "skill_doc")


def read_plugin(router, vault_root, name=None):
    """List plugins, or read a specific plugin file by name."""
    return read_named_resource(router, vault_root, "plugin", name, "plugins", "skill_doc")


def read_memory(router, vault_root, name=None):
    """Read a specific memory by trigger/name (case-insensitive substring).

    Enumeration is owned publicly by ``resource.list``.
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
    return _portable_read_environment(router, vault_root, name)


def read_router_meta(router, vault_root, name=None):
    """Return always-rules and metadata."""
    return _portable_read_router_meta(router, vault_root, name)


def read_artefact(router, vault_root, name=None):
    """Read an artefact file by relative path or basename.

    Full relative paths (containing '/') read any file in the vault directly.
    Bare basenames are resolved via wikilink-style lookup and validated against
    artefact folders.
    """
    return _portable_read_artefact(router, vault_root, name)


# Mapping from _Config/ subfolder to the canonical read-command noun.
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

    key = normalize_artefact_key(name)
    if key:
        entry = resolve_artefact_key_entry(router, key)
        if not entry:
            return {"error": f"No artefact matching '{name}'"}
        return read_file_content(vault_root, entry["path"])

    if "/" in name:
        if is_archived_path(name):
            return {
                "error": f"'{name}' is archived. "
                "Use artefact.read with location='archived'."
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


# ---------------------------------------------------------------------------
# Archive resource
# ---------------------------------------------------------------------------

def read_archive(router, vault_root, name=None):
    """Read a specific archived file by path inside _Archive/.

    Enumeration is owned by ``artefact.list`` with ``location='archived'``.
    """
    _require_name("archive", name)
    return _portable_read_archive(vault_root, name, infer_markdown=True)


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

def _build_parser():
    parser = argparse.ArgumentParser(description="Read one Brain vault resource.")
    parser.add_argument("resource", choices=tuple(RESOURCES))
    parser.add_argument("--name")
    parser.add_argument("--vault")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    vault_root = str(find_vault_root(args.vault))
    router = load_compiled_router(vault_root)
    try:
        result = read_resource(router, vault_root, args.resource, args.name)
    except ValueError as exc:
        parser.error(str(exc))

    if isinstance(result, MissingFileResult):
        print(result, file=sys.stderr)
        return 1
    if isinstance(result, dict) and "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
