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

from _resource_contract import RESOURCE_KINDS
from _common import (
    apply_terminal_status_folder,
    artefact_type_prefix,
    check_write_allowed,
    config_resource_rel_path,
    ensure_parent_tag,
    ensure_self_tag,
    ensure_tags_list,
    find_duplicate_basenames,
    find_vault_root,
    generate_contextual_slug,
    living_key_set,
    load_compiled_router,
    make_temp_path,
    make_artefact_key,
    normalize_artefact_key,
    parse_frontmatter,
    read_file_content,
    reconcile_fields_for_render,
    render_filename_or_default,
    resolve_and_validate_folder,
    resolve_artefact_key_entry,
    resolve_body_file,
    resolve_folder,
    resolve_parent_reference,
    resolve_type,
    safe_write,
    serialize_frontmatter,
    substitute_template_vars,
    title_to_slug,
    unique_filename,
    validate_key,
)
import fix_links as _fix_links


def _generate_key(vault_root, router, artefact, title, explicit=None):
    """Generate or validate a key value for a living artefact."""
    existing = living_key_set(vault_root, router, artefact)
    if explicit is not None:
        explicit = validate_key(explicit)
        if explicit in existing:
            raise ValueError(f"KEY_TAKEN: key '{explicit}' is already used")
        return explicit

    while True:
        candidate = generate_contextual_slug(title)
        if candidate not in existing:
            return candidate


def _build_parent_context(router, artefact, fields, parent_key, parent_entry):
    """Build the advisory ``parent_context`` payload for create responses."""
    if "artefact_index" not in router:
        return None

    artefact_index = router.get("artefact_index") or {}
    if parent_key and parent_entry:
        related = [
            entry["path"]
            for entry in artefact_index.values()
            if entry.get("parent") == parent_key
        ][:3]
        return {
            "placed_under": parent_key,
            "parent_path": parent_entry["path"],
            "related": related,
            "hint": "Consider updating the parent artefact and any roadmap or child index it maintains.",
        }

    tagged = []
    for tag in ensure_tags_list(fields):
        normalized = normalize_artefact_key(tag)
        if normalized and normalized in artefact_index:
            tagged.append(
                {"key": normalized, "path": artefact_index[normalized]["path"]}
            )
    if tagged:
        return {
            "placed_under": None,
            "tagged_artefacts": tagged,
            "hint": "Tags reference other artefacts. If this artefact is owned by one, recreate or move it with parent set.",
        }

    candidate_count = sum(
        1
        for entry in artefact_index.values()
        if entry.get("type") == artefact["frontmatter_type"]
        and entry.get("children_count", 0) > 0
    )
    if candidate_count:
        return {
            "placed_under": None,
            "candidate_count": candidate_count,
            "hint": "This type has existing parent artefacts. If this artefact is owned by one, pass parent using canonical artefact-key form.",
        }
    return None


def create_artefact(vault_root, router, type_key, title, body="", frontmatter_overrides=None, parent=None, key=None, template_vars=None, fix_links=False):
    """Create a new artefact. Returns {"path": relative_path, "type": ..., "title": ...}.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        type_key: Artefact type key (e.g. "idea") or full type (e.g. "living/idea").
        title: Human-readable title, used for filename generation.
        body: Markdown body content (optional, template body used if empty).
        frontmatter_overrides: Optional dict of frontmatter field overrides.
        parent: Optional parent artefact reference for child artefacts.
                Accepts canonical key form (e.g. "project/brain"), or a
                resolvable name/path; persists as canonical `{type}/{key}`.
                Living children then file into same-type `{key}/` folders
                or cross-type `{scope}/` folders. Temporal artefacts keep
                their normal date-based folders.
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

    overrides = dict(frontmatter_overrides or {})
    if key is None and "key" in overrides:
        key = overrides.pop("key")

    artefact = resolve_type(router, type_key)
    template_fields, template_body = _read_template(vault_root, artefact)

    # Capture now once so filename, folder, and timestamps stay consistent.
    now = datetime.now(timezone.utc).astimezone()
    now_iso = now.isoformat()

    # Seed frontmatter before filename generation so naming patterns can
    # reference template/frontmatter values such as {Version}, and so that
    # date tokens resolve from the reconciled ``created`` / ``date_source``
    # field rather than the wallclock.
    fields = dict(template_fields)
    if overrides:
        fields.update(overrides)

    if artefact.get("frontmatter") and artefact["frontmatter"].get("type"):
        fields["type"] = artefact["frontmatter"]["type"]
    if "created" not in fields:
        fields["created"] = now_iso
    if "modified" not in fields:
        fields["modified"] = now_iso

    resolved_parent = None
    parent_entry = None
    if parent:
        resolved_parent, parent_entry = resolve_parent_reference(
            vault_root, router, parent
        )

    if artefact.get("classification") == "living":
        generated_key = _generate_key(vault_root, router, artefact, title, explicit=key)
        fields["key"] = generated_key
        ensure_self_tag(fields, artefact_type_prefix(artefact), generated_key)
    elif key is not None:
        raise ValueError("key override only applies to living artefacts")

    if resolved_parent:
        fields["parent"] = resolved_parent
        ensure_parent_tag(fields)

    reconcile_fields_for_render(fields, artefact)
    filename = render_filename_or_default(artefact.get("naming"), title, fields)
    folder = resolve_folder(
        artefact,
        parent=resolved_parent or parent,
        fields=fields,
        router=router,
    )
    folder = apply_terminal_status_folder(folder, artefact, fields)

    final_body = body if body else template_body
    if not body and final_body:
        final_body = substitute_template_vars(final_body, template_vars, _now=now)

    basename_stem = os.path.splitext(filename)[0]

    # Same-folder: append random 3-char suffix to make filename unique.
    abs_folder = os.path.join(vault_root, folder)
    os.makedirs(abs_folder, exist_ok=True)
    filename = unique_filename(abs_folder, basename_stem)

    # Cross-folder: append type key if original basename exists elsewhere.
    duplicates = find_duplicate_basenames(vault_root, basename_stem, limit=1)
    folder_prefix = os.path.join(folder, "")
    if duplicates and not any(d.startswith(folder_prefix) for d in duplicates):
        current_stem = os.path.splitext(filename)[0]
        filename = f"{current_stem} ({artefact['key']}).md"

    rel_path = os.path.join(folder, filename)
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    content = serialize_frontmatter(fields, body=final_body)
    safe_write(abs_path, content, bounds=vault_root, exclusive=True)

    result = {
        "path": rel_path,
        "type": artefact["type"],
        "title": title,
    }
    if fields.get("key"):
        result["key"] = fields["key"]
    if resolved_parent:
        result["parent"] = resolved_parent
    parent_context = _build_parent_context(
        router, artefact, fields, resolved_parent, parent_entry
    )
    if parent_context:
        result["parent_context"] = parent_context
    _fix_links.attach_wikilink_warnings(vault_root, result, apply_fixes=fix_links)
    return result


# ---------------------------------------------------------------------------
# Resource-aware creation (Phase 4)
# ---------------------------------------------------------------------------

CREATABLE_RESOURCES = RESOURCE_KINDS


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

    if resource not in CREATABLE_RESOURCES:
        raise ValueError(
            f"Resource '{resource}' is not creatable via brain_create. "
            f"Creatable resources: {', '.join(CREATABLE_RESOURCES)}"
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
