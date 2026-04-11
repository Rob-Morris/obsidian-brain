#!/usr/bin/env python3
"""
create.py — Create vault artefacts and _Config/ resources.

Artefact creation resolves type from the compiled router, reads the template,
generates a filename from the naming pattern, and writes the file with
frontmatter.

Resource creation (skill, memory, style, template) writes to the appropriate
_Config/ subfolder following each resource kind's conventions.

Usage:
    python3 create.py --type idea --title "My Idea"
    python3 create.py --type idea --title "My Idea" --body "Content here"
    python3 create.py --type idea --title "My Idea" --vault /path/to/vault --json
    python3 create.py --resource skill --name my-skill --body "Skill content"
"""

import json
import os
import sys
from datetime import datetime, timezone

from _common import (
    check_write_allowed,
    config_resource_rel_path,
    find_duplicate_basenames,
    find_vault_root,
    load_compiled_router,
    make_temp_path,
    parse_frontmatter,
    read_file_content,
    resolve_body_file,
    resolve_folder,
    resolve_naming_pattern,
    resolve_type,
    safe_write,
    serialize_frontmatter,
    substitute_template_vars,
    title_to_slug,
    unique_filename,
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def create_artefact(vault_root, router, type_key, title, body="", frontmatter_overrides=None, parent=None, template_vars=None):
    """Create a new artefact. Returns {"path": relative_path, "type": ..., "title": ...}.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        type_key: Artefact type key (e.g. "idea") or full type (e.g. "living/idea").
        title: Human-readable title, used for filename generation.
        body: Markdown body content (optional, template body used if empty).
        frontmatter_overrides: Optional dict of frontmatter field overrides.
        parent: Optional project subfolder name for living types (e.g. "Brain").
                Places the artefact in {Type}/{parent}/ instead of {Type}/.
        template_vars: Optional dict of placeholder→value substitutions applied
                to the template body (e.g. {"SOURCE_TYPE": "designs"}).
                ``{{date:FORMAT}}`` placeholders are always substituted when the
                template body is used.

    Returns:
        Dict with path, type, and title.

    Raises:
        ValueError: If type is not found, not configured, or file already exists.
    """
    vault_root = str(vault_root)

    # 1. Resolve type
    artefact = resolve_type(router, type_key)

    # 2. Read template (base frontmatter + body)
    template_fields, template_body = _read_template(vault_root, artefact)

    # 3. Capture now once for consistent filename, folder, and timestamps
    now = datetime.now(timezone.utc).astimezone()
    now_iso = now.isoformat()

    # 4. Generate filename
    pattern = artefact.get("naming", {}).get("pattern") if artefact.get("naming") else None
    if pattern:
        filename = resolve_naming_pattern(pattern, title, _now=now)
    else:
        filename = title_to_filename(title) + ".md"

    # 5. Resolve folder
    folder = resolve_folder(artefact, parent=parent, _now=now)

    # 6. Merge frontmatter: template → overrides → force type → timestamps
    fields = dict(template_fields)
    if frontmatter_overrides:
        fields.update(frontmatter_overrides)
    if artefact.get("frontmatter") and artefact["frontmatter"].get("type"):
        fields["type"] = artefact["frontmatter"]["type"]
    if "created" not in fields:
        fields["created"] = now_iso
    if "modified" not in fields:
        fields["modified"] = now_iso

    # 7. Determine body and substitute template variables
    final_body = body if body else template_body
    if not body and final_body:
        final_body = substitute_template_vars(final_body, template_vars, _now=now)

    # 8. Disambiguate basename collisions
    basename_stem = os.path.splitext(filename)[0]

    # Same-folder: append random 3-char suffix to make filename unique
    abs_folder = os.path.join(vault_root, folder)
    os.makedirs(abs_folder, exist_ok=True)
    filename = unique_filename(abs_folder, basename_stem)

    # Cross-folder: append type key if original basename exists elsewhere
    duplicates = find_duplicate_basenames(vault_root, basename_stem, limit=1)
    folder_prefix = os.path.join(folder, "")
    if duplicates and not any(d.startswith(folder_prefix) for d in duplicates):
        current_stem = os.path.splitext(filename)[0]
        filename = f"{current_stem} ({artefact['key']}).md"

    # 9. Write
    rel_path = os.path.join(folder, filename)
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    content = serialize_frontmatter(fields, body=final_body)
    safe_write(abs_path, content, bounds=vault_root, exclusive=True)

    return {"path": rel_path, "type": artefact["type"], "title": title}


# ---------------------------------------------------------------------------
# Resource-aware creation (Phase 4)
# ---------------------------------------------------------------------------

_CREATABLE_RESOURCES = {"artefact", "skill", "memory", "style", "template"}


def create_resource(vault_root, router, resource="artefact", **kwargs):
    """Create a vault resource. Dispatches to resource-specific creators.

    For artefacts: delegates to create_artefact() with type_key, title, body, etc.
    For other resources: creates in the appropriate _Config/ subfolder.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        resource: Resource kind — one of: artefact, skill, memory, style, template.
        **kwargs: Resource-specific params (see individual creators).

    Returns:
        Dict with path, resource (or type for artefacts), and name (or title).

    Raises:
        ValueError: If resource is not creatable or required params are missing.
    """
    if resource == "artefact":
        return create_artefact(vault_root, router, **kwargs)

    if resource not in _CREATABLE_RESOURCES:
        raise ValueError(
            f"Resource '{resource}' is not creatable via brain_create. "
            f"Creatable resources: {', '.join(sorted(_CREATABLE_RESOURCES))}"
        )

    name = kwargs.get("name")
    body = kwargs.get("body")
    frontmatter = kwargs.get("frontmatter")

    if not name:
        raise ValueError(f"brain_create(resource='{resource}') requires name.")
    if not body:
        raise ValueError(f"brain_create(resource='{resource}') requires body.")

    return _RESOURCE_CREATORS[resource](vault_root, router, name, body, frontmatter)


def _create_config_resource(vault_root, resource, rel_path, name, body, frontmatter, exclusive=True):
    """Shared logic for creating a _Config/ resource file.

    Handles write-guard, content serialisation, and safe_write.
    All _Config/ resources are always serialised with frontmatter.
    """
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    content = serialize_frontmatter(dict(frontmatter) if frontmatter else {}, body=body)
    try:
        safe_write(abs_path, content, bounds=vault_root, exclusive=exclusive)
    except FileExistsError:
        raise ValueError(
            f"{resource.capitalize()} '{name}' already exists at {rel_path}"
        )
    return {"path": rel_path, "resource": resource, "name": name}

def _create_skill(vault_root, router, name, body, frontmatter):
    """Create a skill at _Config/Skills/{slug}/SKILL.md."""
    rel_path = config_resource_rel_path(router, "skill", name)
    slug = title_to_slug(name)
    return _create_config_resource(vault_root, "skill", rel_path, slug, body, frontmatter)


def _create_memory(vault_root, router, name, body, frontmatter):
    """Create a memory at _Config/Memories/{slug}.md."""
    rel_path = config_resource_rel_path(router, "memory", name)
    slug = title_to_slug(name)
    return _create_config_resource(vault_root, "memory", rel_path, slug, body, frontmatter)


def _create_style(vault_root, router, name, body, frontmatter):
    """Create a style at _Config/Styles/{slug}.md."""
    rel_path = config_resource_rel_path(router, "style", name)
    slug = title_to_slug(name)
    return _create_config_resource(vault_root, "style", rel_path, slug, body, frontmatter)


def _create_template(vault_root, router, name, body, frontmatter):
    """Create a template for an artefact type.

    name is the artefact type key (e.g. "wiki"). Resolves the classification
    and folder from the router to place the template at the correct path.
    """
    rel_path = config_resource_rel_path(router, "template", name)
    # Templates may be overwritten (updating an existing template)
    return _create_config_resource(
        vault_root, "template", rel_path, name, body, frontmatter, exclusive=False,
    )


_RESOURCE_CREATORS = {
    "skill": _create_skill,
    "memory": _create_memory,
    "style": _create_style,
    "template": _create_template,
}

def _read_template(vault_root, artefact):
    """Read and parse the template file for an artefact type."""
    template_ref = artefact.get("template_file")
    if not template_ref:
        return {}, ""

    content = read_file_content(vault_root, template_ref)
    if content.startswith("Error:"):
        return {}, ""

    return parse_frontmatter(content)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    type_key = None
    title = None
    body = ""
    body_file_path = ""
    vault_arg = None
    parent = None
    json_mode = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--type" and i + 1 < len(sys.argv):
            type_key = sys.argv[i + 1]
            i += 2
        elif arg == "--title" and i + 1 < len(sys.argv):
            title = sys.argv[i + 1]
            i += 2
        elif arg == "--body" and i + 1 < len(sys.argv):
            body = sys.argv[i + 1]
            i += 2
        elif arg == "--body-file" and i + 1 < len(sys.argv):
            body_file_path = sys.argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        elif arg == "--parent" and i + 1 < len(sys.argv):
            parent = sys.argv[i + 1]
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        elif arg == "--temp-path":
            suffix = ".md"
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                suffix = sys.argv[i + 1]
            print(make_temp_path(suffix=suffix))
            sys.exit(0)
        else:
            i += 1

    if not type_key or not title:
        print(
            'Usage: create.py --type TYPE --title TITLE [--body BODY] [--body-file PATH] [--parent NAME] [--vault PATH] [--json] [--temp-path [SUFFIX]]',
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        body, _ = resolve_body_file(body, body_file_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))

    # Load router
    router = load_compiled_router(vault_root)
    if "error" in router:
        if json_mode:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    try:
        result = create_artefact(vault_root, router, type_key, title, body=body, parent=parent)
    except ValueError as e:
        if json_mode:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        print(f"Created {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
