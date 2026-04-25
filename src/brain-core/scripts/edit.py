#!/usr/bin/env python3
"""
edit.py — Edit, append, or prepend to vault artefacts and _Config/ resources.

Validates paths against the compiled router, then modifies file content
with frontmatter preservation. Also provides artefact type conversion.

Usage:
    python3 edit.py edit --path "Wiki/my-page.md" --body "New body"
    python3 edit.py append --path "Wiki/my-page.md" --body "Appended text"
    python3 edit.py prepend --path "Wiki/my-page.md" --body "Before existing"
    python3 edit.py edit --path "Wiki/my-page.md" --body "New body" --vault /path --json
"""

import json
import os
import re
import sys

from _common import (
    SELF_TAG_PREFIXES,
    check_write_allowed,
    config_resource_rel_path,
    ensure_parent_tag,
    ensure_self_tag,
    ensure_tags_list,
    extract_slug_keyword,
    extract_title,
    find_body_preamble,
    find_section,
    find_vault_root,
    generate_contextual_slug,
    is_archived_path,
    is_valid_key,
    iter_living_markdown_files,
    living_key_set,
    load_compiled_router,
    make_artefact_key,
    make_wikilink_replacer,
    make_temp_path,
    normalize_artefact_key,
    now_iso,
    parse_frontmatter,
    read_file_content,
    replace_artefact_key_references,
    reconcile_fields_for_render,
    render_filename,
    render_filename_or_default,
    replace_wikilinks_in_vault,
    resolve_artefact_definition_for_prefix,
    resolve_folder,
    resolve_and_validate_folder,
    resolve_parent_reference,
    resolve_body_file,
    resolve_type,
    resolve_wikilink_stems,
    scan_artefact_key_references,
    safe_write,
    serialize_frontmatter,
    parse_structural_anchor_line,
    strip_md_ext,
    unique_filename,
    validate_key,
    artefact_type_prefix,
)
from rename import rename_and_update_links
import fix_links as _fix_links


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPERATION_LABELS = {
    "edit": "Edited",
    "append": "Appended",
    "prepend": "Prepended",
    "delete_section": "Deleted section from",
}

LEGACY_BODY_TARGET = ":body"
ENTIRE_BODY_TARGET = ":entire_body"
BODY_PREAMBLE_TARGET = ":body_preamble"

_RESERVED_NON_SECTION_TARGETS = {
    ENTIRE_BODY_TARGET,
    BODY_PREAMBLE_TARGET,
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _open_artefact(vault_root, router, path):
    """Validate, read, and parse an artefact. Returns (path, abs_path, fields, body, artefact)."""
    vault_root = str(vault_root)
    path, art = resolve_and_validate_folder(vault_root, router, path)
    check_write_allowed(path)
    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)
    return path, abs_path, fields, body, art


def _line_count(text):
    """Count body lines for response summaries."""
    return len(text.splitlines()) if text else 0


def _result_payload(path, resolved_path, operation, old_body, new_body):
    """Build the standard result payload for body mutations."""
    return {
        "path": path,
        "resolved_path": resolved_path,
        "operation": operation,
        "old_body_line_count": _line_count(old_body),
        "new_body_line_count": _line_count(new_body),
    }


def uses_heading_context(target):
    """Return whether a target should show surrounding heading context."""
    return bool(target) and target not in _RESERVED_NON_SECTION_TARGETS


def _validate_target_contract(operation, target):
    """Validate reserved-target semantics for body operations."""
    if not target:
        return

    if target == LEGACY_BODY_TARGET:
        if operation == "delete_section":
            raise ValueError(
                "target=':body' is no longer valid because it was ambiguous and destructive. "
                "delete_section requires a real heading or callout target."
            )
        raise ValueError(
            "target=':body' is no longer valid because it was ambiguous and destructive. "
            "Use target=':entire_body' for the full markdown body after frontmatter "
            "or target=':body_preamble' for the leading body content before the first "
            "targetable section."
        )

    if target == ":body_before_first_heading":
        raise ValueError(
            "target=':body_before_first_heading' is no longer valid. "
            "Use target=':body_preamble' for the leading body content before the first "
            "targetable section."
        )

    if target == BODY_PREAMBLE_TARGET and operation != "edit":
        raise ValueError(
            "target=':body_preamble' is only supported for operation='edit'"
        )


def _validate_edit_request(operation, body, frontmatter_changes=None, target=None):
    """Validate a public edit request before mutating content."""
    _validate_target_contract(operation, target)

    if operation == "delete_section":
        if not target:
            raise ValueError("delete_section requires a target heading.")
        return

    if operation == "edit":
        if not body and not frontmatter_changes and not target:
            raise ValueError(
                "edit with no body and no frontmatter changes is a no-op. "
                "Pass body content, frontmatter changes, or both."
            )
        return

    if operation in {"append", "prepend"} and not body and not frontmatter_changes:
        raise ValueError(
            f"{operation} with no body and no frontmatter changes is a no-op. "
            "Pass body content, frontmatter changes, or both."
        )


def _merge_frontmatter(fields, changes, operation):
    """Merge frontmatter changes using operation-appropriate strategy.

    edit: overwrite all fields (set semantics).
    append/prepend: extend list fields with dedup, overwrite scalars.
    null: delete the field (all operations).

    Side-effect: sets ``statusdate`` to today when *status* actually changes.
    """
    if not changes:
        return
    # Auto-set statusdate when status actually changes value (not on deletion)
    if "status" in changes and changes["status"] is not None and changes["status"] != fields.get("status"):
        fields["statusdate"] = now_iso()[:10]
    for key, value in changes.items():
        if value is None:
            fields.pop(key, None)
        elif operation != "edit" and isinstance(value, list) and isinstance(fields.get(key), list):
            fields[key].extend(v for v in value if v not in fields[key])
        else:
            fields[key] = value


def _save_artefact(abs_path, fields, new_body, vault_root):
    """Set modified timestamp, serialize, and write."""
    fields["modified"] = now_iso()
    new_content = serialize_frontmatter(fields, body=new_body)
    safe_write(abs_path, new_content, bounds=vault_root)


# ---------------------------------------------------------------------------
# Body operation helpers (shared by artefact and resource paths)
# ---------------------------------------------------------------------------

def _normalize_range_replacement(body, end, existing_body, empty_noop=False):
    """Normalize a replacement body for an in-place range splice.

    Ensures a trailing newline, plus a blank-line separator when the range is
    not at EOF. When ``empty_noop`` is True, an empty body produces an empty
    string (the caller's surrounding content provides any needed separator).
    """
    if body:
        normalized = body.rstrip("\n") + "\n"
        if end < len(existing_body):
            normalized += "\n"
        return normalized
    if empty_noop:
        return ""
    return "" if end == len(existing_body) else "\n"


def _apply_edit(existing_body, body, target):
    """Apply an edit operation to a body, returning the new body."""
    if target == ENTIRE_BODY_TARGET:
        return body
    if target == BODY_PREAMBLE_TARGET:
        return _apply_body_preamble_edit(existing_body, body)
    if target:
        section_mode, resolved_target = _resolve_edit_target(target)
        start, end = find_section(
            existing_body,
            resolved_target,
            include_heading=section_mode,
        )
        if section_mode:
            _validate_whole_section_replacement(body)
        else:
            body = _normalize_targeted_edit_body(existing_body, resolved_target, body)
        normalized = _normalize_range_replacement(body, end, existing_body)
        return existing_body[:start] + normalized + existing_body[end:]
    if body:
        return body
    return existing_body


def _apply_body_preamble_edit(existing_body, body):
    """Replace the leading body range before the first targetable section."""
    start, end = find_body_preamble(existing_body)
    normalized = _normalize_range_replacement(body, end, existing_body, empty_noop=True)
    return existing_body[:start] + normalized + existing_body[end:]


def _resolve_edit_target(target):
    """Return (whole_section_mode, resolved_target)."""
    if target.startswith(":section:"):
        resolved = target[len(":section:"):].strip()
        if not resolved:
            raise ValueError("Section replacement target must include a heading or callout title")
        return True, resolved
    return False, target


def _validate_whole_section_replacement(body):
    """Whole-section replacement requires a leading structural anchor."""
    if not body:
        raise ValueError("Whole-section replacement body cannot be empty")
    anchor = parse_structural_anchor_line(body)
    if anchor is None:
        raise ValueError(
            "Whole-section replacement body must begin with a heading or callout title line"
        )


def _normalize_targeted_edit_body(existing_body, target, body):
    """Normalise or reject structural wrappers for content-only targeted edits."""
    if not body:
        return body

    anchor = parse_structural_anchor_line(body)
    if anchor is None:
        return body

    heading_start, section_start = find_section(existing_body, target, include_heading=True)
    expected_anchor = parse_structural_anchor_line(existing_body[heading_start:section_start])
    if expected_anchor and anchor["raw"] == expected_anchor["raw"]:
        return _strip_exact_structural_wrapper(body, anchor["kind"])

    if anchor["kind"] == "callout":
        return body

    if (
        expected_anchor
        and expected_anchor["kind"] == "heading"
        and anchor["kind"] == "heading"
        and anchor["level"] > expected_anchor["level"]
    ):
        return body

    raise ValueError(
        f"Targeted edit for '{target}' replaces section content only; "
        f"use target=':section:{target}' to replace the section heading or callout title"
    )


def _strip_exact_structural_wrapper(body, anchor_kind):
    """Strip one exact leading heading/callout wrapper and following blank lines."""
    lines = body.splitlines(keepends=True)
    out = []
    removed = False
    for line in lines:
        if not removed and not line.strip():
            continue
        if not removed:
            anchor = parse_structural_anchor_line(line)
            if anchor is None or anchor["kind"] != anchor_kind:
                return body
            removed = True
            continue
        out.append(line)

    while out and not out[0].strip():
        out.pop(0)
    return "".join(out)


def _resolve_positional_target(target):
    """Normalize target for append/prepend: strip :section: prefix, drop entire-body sentinel."""
    if target and target.startswith(":section:"):
        target = target[len(":section:"):].strip()
    if target == ENTIRE_BODY_TARGET:
        return None
    return target


def _apply_append(existing_body, content, target):
    """Apply an append operation to a body, returning the new body."""
    target = _resolve_positional_target(target)
    if target and content:
        section_start, section_end = find_section(existing_body, target)
        section_body = existing_body[section_start:section_end].rstrip("\n")
        content_normalized = content if content.endswith("\n") else content + "\n"
        if section_body:
            rebuilt = section_body + "\n" + content_normalized
        else:
            rebuilt = "\n" + content_normalized
        if section_end < len(existing_body):
            rebuilt += "\n"
        return existing_body[:section_start] + rebuilt + existing_body[section_end:]
    if content:
        return existing_body + content
    return existing_body


def _apply_prepend(existing_body, content, target):
    """Apply a prepend operation to a body, returning the new body."""
    target = _resolve_positional_target(target)
    if target and content:
        heading_start, _ = find_section(existing_body, target, include_heading=True)
        content_normalized = content.rstrip("\n") + "\n"
        if heading_start > 0:
            content_normalized += "\n"
        return existing_body[:heading_start] + content_normalized + existing_body[heading_start:]
    if content:
        content_normalized = content if content.endswith("\n") else content + "\n"
        return content_normalized + ("\n" if existing_body else "") + existing_body
    return existing_body


def _apply_delete_section(existing_body, target):
    """Apply a delete_section operation to a body, returning the new body."""
    start, end = find_section(existing_body, target, include_heading=True)
    prefix = existing_body[:start].rstrip("\n")
    suffix = existing_body[end:]
    if prefix and suffix:
        return prefix + "\n\n" + suffix
    if prefix:
        return prefix + "\n"
    return suffix


# ---------------------------------------------------------------------------
# Resource-aware editing (Phase 5)
# ---------------------------------------------------------------------------

_EDITABLE_RESOURCES = {"artefact", "skill", "memory", "style", "template"}

_BODY_OPS = {
    "edit": _apply_edit,
    "append": _apply_append,
    "prepend": _apply_prepend,
    "delete_section": _apply_delete_section,
}


def edit_resource(vault_root, router, resource="artefact", operation="edit",
                  path=None, name=None, body="", frontmatter_changes=None,
                  target=None, fix_links=False):
    """Edit a vault resource. Dispatches to the appropriate handler.

    For artefacts: delegates to existing edit/append/prepend/delete_section functions.
    For other resources: resolves path via _Config/ conventions, applies the
    same edit operations without artefact-specific behavior (no terminal status
    auto-move, no modified timestamp injection).

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        resource: Resource kind — one of: artefact, skill, memory, style, template.
        operation: "edit", "append", "prepend", or "delete_section".
        path: Relative path (artefacts only).
        name: Resource name (non-artefact resources only).
        body: Content for the operation.
        frontmatter_changes: Optional dict of frontmatter field changes.
        target: Optional heading/callout for targeted operations, or reserved
                body targets such as ":entire_body" and ":body_preamble".

    Returns:
        Dict with path and operation.
    """
    vault_root = str(vault_root)

    if resource == "artefact":
        if not path:
            raise ValueError("path is required when resource='artefact'")
        if operation == "edit":
            result = edit_artefact(vault_root, router, path, body,
                                frontmatter_changes=frontmatter_changes, target=target)
        elif operation == "append":
            result = append_to_artefact(vault_root, router, path, body,
                                     frontmatter_changes=frontmatter_changes, target=target)
        elif operation == "prepend":
            result = prepend_to_artefact(vault_root, router, path, body,
                                      frontmatter_changes=frontmatter_changes, target=target)
        elif operation == "delete_section":
            result = delete_section_artefact(vault_root, router, path,
                                          target=target, frontmatter_changes=frontmatter_changes)
        else:
            raise ValueError(f"Unknown operation '{operation}'")
        _fix_links.attach_wikilink_warnings(vault_root, result, apply_fixes=fix_links)
        return result

    if resource not in _EDITABLE_RESOURCES:
        raise ValueError(
            f"Resource '{resource}' is not editable via brain_edit. "
            f"Editable resources: {', '.join(sorted(_EDITABLE_RESOURCES))}"
        )

    if not name:
        raise ValueError(f"brain_edit(resource='{resource}') requires name.")

    # Resolve and read config resource
    rel_path = config_resource_rel_path(router, resource, name)
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{resource.capitalize()} '{name}' not found at {rel_path}"
        ) from None
    fields, existing_body = parse_frontmatter(content)

    # Apply operation using shared helpers
    apply_fn = _BODY_OPS.get(operation)
    if not apply_fn:
        raise ValueError(f"Unknown operation '{operation}'")

    _validate_edit_request(operation, body, frontmatter_changes, target)
    fm_mode = "edit" if operation in ("edit", "delete_section") else operation
    _merge_frontmatter(fields, frontmatter_changes, fm_mode)

    if operation == "delete_section":
        new_body = apply_fn(existing_body, target)
    else:
        new_body = apply_fn(existing_body, body, target)

    # Save without artefact-specific behavior (no modified auto-set, no status move)
    new_content = serialize_frontmatter(fields, body=new_body)
    safe_write(abs_path, new_content, bounds=vault_root)

    return _result_payload(rel_path, rel_path, operation, existing_body, new_body)


def _replace_exact_tag(fields, old_tag, new_tag=None):
    """Replace or remove an exact tag match, preserving order."""
    tags = ensure_tags_list(fields)
    updated = []
    changed = False
    for tag in tags:
        if tag != old_tag:
            updated.append(tag)
            continue
        changed = True
        if new_tag and new_tag not in updated:
            updated.append(new_tag)
    if new_tag and new_tag not in updated:
        updated.append(new_tag)
        changed = True
    fields["tags"] = updated
    return changed


def _derive_title_from_path(art, fields, path):
    """Resolve a human title for filename rendering."""
    title = fields.get("title")
    if title:
        return title
    stem = os.path.splitext(os.path.basename(path))[0]
    return extract_title(art.get("naming"), fields, stem) or stem


def _terminal_status_folder(art, fields):
    """Return the canonical +Status folder for terminal artefacts, if any."""
    terminal = ((art or {}).get("frontmatter") or {}).get("terminal_statuses") or []
    status = (fields or {}).get("status")
    if status in terminal:
        return f"+{status.capitalize()}"
    return None


def _render_existing_artefact_path(vault_root, router, art, path, fields):
    """Render the canonical path for an existing artefact from its fields."""
    current_basename = os.path.basename(path)
    abs_path = os.path.join(vault_root, path)
    title = _derive_title_from_path(art, fields, path)
    rendered_fields = dict(fields)
    reconcile_fields_for_render(rendered_fields, art, abs_path, current_basename)
    folder = resolve_folder(
        art,
        parent=normalize_artefact_key(rendered_fields.get("parent")),
        fields=rendered_fields,
        router=router,
    )
    status_folder = _terminal_status_folder(art, rendered_fields)
    if status_folder:
        folder = os.path.join(folder, status_folder)
    basename = render_filename_or_default(art.get("naming"), title, rendered_fields)
    return os.path.join(folder, basename), rendered_fields


def _ensure_free_artefact_key(vault_root, router, art, key, *, exclude_path=None):
    """Fail if ``key`` is already used by another artefact of this type."""
    existing = living_key_set(vault_root, router, art, exclude_path=exclude_path)
    if key in existing:
        raise ValueError(f"KEY_TAKEN: key '{key}' is already used")


def _choose_living_key(vault_root, router, art, title, key=None, *, exclude_path=None):
    """Return a collision-free living key for ``art``."""
    existing = living_key_set(vault_root, router, art, exclude_path=exclude_path)
    if key is not None:
        key = validate_key(key)
        if key in existing:
            raise ValueError(f"KEY_TAKEN: key '{key}' is already used")
        return key
    while True:
        candidate = generate_contextual_slug(title)
        if candidate not in existing:
            return candidate


def _normalise_ownership_changes(vault_root, router, art, frontmatter_changes):
    """Canonicalise key and parent changes before merging frontmatter."""
    if not frontmatter_changes:
        return frontmatter_changes

    changes = dict(frontmatter_changes)
    classification = art.get("classification")

    if "key" in changes:
        if classification != "living":
            raise ValueError("key changes only apply to living artefacts")
        if changes["key"] in (None, ""):
            raise ValueError("key cannot be removed from a living artefact")
        changes["key"] = validate_key(changes["key"])

    if "parent" in changes:
        if changes["parent"] in (None, ""):
            changes["parent"] = None
        else:
            resolved_parent, _entry = resolve_parent_reference(
                vault_root, router, changes["parent"]
            )
            changes["parent"] = resolved_parent

    return changes


def _preflight_destination(vault_root, source_path, dest_path):
    """Raise if a planned destination already exists on disk."""
    if dest_path == source_path:
        return
    abs_source = os.path.join(vault_root, source_path)
    abs_dest = os.path.join(vault_root, dest_path)
    if not os.path.exists(abs_dest):
        return
    try:
        same = os.path.samefile(abs_source, abs_dest)
    except OSError:
        same = False
    if not same:
        raise FileExistsError(f"Destination file already exists: {dest_path}")


def _router_with_pending_artefact_key(router, old_key, new_key, entry):
    """Return a router view whose artefact index reflects an in-flight key update."""
    updated_router = dict(router)
    artefact_index = dict(router.get("artefact_index") or {})
    if old_key:
        artefact_index.pop(old_key, None)
    artefact_index[new_key] = entry
    updated_router["artefact_index"] = artefact_index
    return updated_router


def _commit_with_possible_rename(vault_root, path, new_path, fields, body):
    """Serialize + safe_write frontmatter and body, then rename if path changed.

    Caller handles _preflight_destination; this helper is the commit step.
    """
    abs_path = os.path.join(vault_root, path)
    safe_write(
        abs_path,
        serialize_frontmatter(fields, body=body),
        bounds=vault_root,
    )
    if new_path != path:
        rename_and_update_links(vault_root, path, new_path)


def _apply_reference_mutation(vault_root, router, old_key, new_key, *, skip_paths=None):
    """Rewrite canonical key references and move affected direct children."""
    if not old_key or old_key == new_key:
        return []

    skip_paths = set(skip_paths or [])
    operations = []
    for ref in scan_artefact_key_references(vault_root, router, old_key):
        rel_path = ref["path"]
        if rel_path in skip_paths:
            continue
        content = read_file_content(vault_root, rel_path)
        if content.startswith("Error:"):
            continue
        fields, body = parse_frontmatter(content)
        if not replace_artefact_key_references(fields, old_key, new_key):
            continue
        _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        new_path = rel_path
        if ref.get("parent"):
            new_path, fields = _render_existing_artefact_path(
                vault_root, router, art, rel_path, fields
            )
        operations.append(
            {
                "path": rel_path,
                "new_path": new_path,
                "fields": fields,
                "body": body,
            }
        )

    for op in operations:
        _preflight_destination(vault_root, op["path"], op["new_path"])

    for op in operations:
        _commit_with_possible_rename(
            vault_root, op["path"], op["new_path"], op["fields"], op["body"]
        )

    return operations


def _maybe_restructure_living_ownership(vault_root, router, path, art, old_fields, new_fields):
    """Rewrite canonical key references and move artefacts when ownership changes."""
    old_slug = old_fields.get("key")
    new_slug = new_fields.get("key")
    old_parent = normalize_artefact_key(old_fields.get("parent"))
    new_parent = normalize_artefact_key(new_fields.get("parent"))
    type_prefix = artefact_type_prefix(art)
    old_key = (
        make_artefact_key(type_prefix, old_slug)
        if is_valid_key(old_slug)
        else None
    )
    new_key = (
        make_artefact_key(type_prefix, new_slug)
        if is_valid_key(new_slug)
        else None
    )

    ownership_changed = old_key != new_key or old_parent != new_parent
    if not ownership_changed:
        return path, False

    if old_key and old_key != new_key:
        replacement = new_key if type_prefix in SELF_TAG_PREFIXES else None
        _replace_exact_tag(new_fields, old_key, replacement)
    elif new_key:
        ensure_self_tag(new_fields, type_prefix, new_slug)

    if new_key:
        _ensure_free_artefact_key(
            vault_root, router, art, new_slug, exclude_path=path
        )

    new_path, rendered_fields = _render_existing_artefact_path(
        vault_root, router, art, path, new_fields
    )

    mutation_router = router
    if old_key and new_key:
        # The inbound-reference scan below uses folder derivations from the
        # router's artefact_index entry for this key. Swap in a pending entry
        # under the new key so children resolve their forthcoming positions
        # (new folder, new parent pointer) rather than the now-stale old ones.
        old_entry = (router.get("artefact_index") or {}).get(old_key) or {}
        pending_entry = dict(old_entry)
        pending_entry.update(
            {
                "path": new_path,
                "type": art.get("frontmatter_type", art.get("type")),
                "type_key": art.get("key"),
                "type_prefix": type_prefix,
                "key": new_slug,
                "parent": new_parent,
            }
        )
        mutation_router = _router_with_pending_artefact_key(
            router, old_key, new_key, pending_entry
        )
        _apply_reference_mutation(
            vault_root, mutation_router, old_key, new_key, skip_paths={path}
        )

    _preflight_destination(vault_root, path, new_path)
    abs_path = os.path.join(vault_root, path)
    _commit_with_possible_rename(
        vault_root, path, new_path, rendered_fields, _read_body(abs_path)
    )
    if new_path != path:
        return new_path, True
    return path, True


def _read_body(abs_path):
    """Return the markdown body from an artefact file on disk."""
    with open(abs_path, "r", encoding="utf-8") as f:
        _fields, body = parse_frontmatter(f.read())
    return body


def _maybe_status_move(vault_root, path, terminal_statuses, frontmatter_changes):
    """If frontmatter_changes sets a terminal status, move file to +Status/ folder.

    Returns new path if moved, or original path if not.
    """
    if not frontmatter_changes or "status" not in frontmatter_changes:
        return path

    if not terminal_statuses:
        return path

    if is_archived_path(path):
        return path  # _Archive/ is a manual location; auto-move does not apply

    new_status = frontmatter_changes["status"]
    parent_dir = os.path.dirname(path)
    filename = os.path.basename(path)
    parent_name = os.path.basename(parent_dir)

    if new_status in terminal_statuses:
        # Terminal → move into +Status/ folder
        status_folder = f"+{new_status.capitalize()}"
        if parent_name == status_folder:
            return path  # already in correct folder
        # If already inside a +Status/ folder, resolve relative to grandparent
        # to avoid nesting (e.g. +Implemented/+Superseded/ → +Superseded/)
        base_dir = os.path.dirname(parent_dir) if parent_name.startswith("+") else parent_dir
        new_path = os.path.join(base_dir, status_folder, filename)
    elif parent_name.startswith("+"):
        # Non-terminal and currently in a +Status/ folder → move out
        grandparent = os.path.dirname(parent_dir)
        new_path = os.path.join(grandparent, filename)
    else:
        return path

    rename_and_update_links(vault_root, path, new_path)

    # Clean up empty +Status/ folder after revive
    if parent_name.startswith("+"):
        abs_old_dir = os.path.join(vault_root, parent_dir)
        try:
            os.rmdir(abs_old_dir)  # only removes if empty
        except OSError:
            pass

    return new_path


def _apply_status_change_hooks(fields, old_fields, art):
    """Apply ``{status}_at`` convention and ``on_status_change`` hooks.

    When ``status`` changes value, set ``{status}_at = now()`` (ISO date) for
    the new status unless the type's ``on_status_change`` hook overrides the
    field name. Also backfills ``{status}_at`` when a status is observed for
    the first time without its timestamp (reconcile path).
    """
    new_status = fields.get("status")
    old_status = (old_fields or {}).get("status")
    if not new_status:
        return
    changed = new_status != old_status
    if not changed:
        return
    today = now_iso()[:10]
    hook = ((art or {}).get("on_status_change") or {}).get(new_status) or {}
    set_map = hook.get("set") or {}
    for field_name, raw_value in set_map.items():
        if fields.get(field_name):
            continue
        value = today if str(raw_value).lower() in ("now", "today") else raw_value
        fields[field_name] = value
    default_field = f"{new_status}_at"
    if default_field not in set_map and not fields.get(default_field):
        fields[default_field] = today


def _maybe_relocate_temporal_month(vault_root, path, art, fields):
    """Relocate a temporal artefact to ``_Temporal/<Type>/yyyy-mm/`` for its ``created``.

    Returns the (possibly-updated) path. No-op for living artefacts, archived
    files, or artefacts already in the correct month folder.
    """
    if (art or {}).get("classification") != "temporal":
        return path
    if is_archived_path(path):
        return path
    try:
        target_folder = resolve_folder(art, fields=fields)
    except ValueError:
        return path
    current_folder = os.path.dirname(path)
    if current_folder == target_folder:
        return path
    new_path = os.path.join(target_folder, os.path.basename(path))
    abs_target = os.path.join(vault_root, target_folder)
    os.makedirs(abs_target, exist_ok=True)
    rename_and_update_links(vault_root, path, new_path)
    return new_path


def _maybe_rename_on_field_change(vault_root, path, art, old_fields, new_fields):
    """Rename artefact file if frontmatter changes imply a new basename.

    Extracts the title from the current basename using the rule selected for
    the *old* fields, then re-renders using the *new* fields. If the resulting
    basename differs, rename in place (same directory) and update wikilinks.
    Archived files are exempt (they carry an archival prefix outside the
    naming contract).
    """
    naming = art.get("naming")
    if not naming:
        return path
    if is_archived_path(path):
        return path
    current_basename = os.path.basename(path)
    title = extract_title(naming, old_fields, current_basename)
    if title is None:
        title = os.path.splitext(current_basename)[0]
    try:
        new_basename = render_filename(naming, title, new_fields)
    except ValueError:
        return path
    if new_basename == current_basename:
        return path
    new_path = os.path.join(os.path.dirname(path), new_basename)
    rename_and_update_links(vault_root, path, new_path)
    return new_path


def _finish_artefact(vault_root, router, abs_path, fields, old_body, new_body, path, art,
                     frontmatter_changes, operation, old_fields=None):
    """Save artefact, rename on name-driving change, status-move, return result."""
    _apply_status_change_hooks(fields, old_fields, art)
    had_explicit_created = bool((old_fields or {}).get("created")) or bool(
        (frontmatter_changes or {}).get("created")
    )
    reconcile_fields_for_render(fields, art, abs_path, os.path.basename(path))
    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    if art.get("classification") == "temporal" and had_explicit_created:
        new_path = _maybe_relocate_temporal_month(vault_root, path, art, fields)
        if new_path != path:
            path = new_path
            abs_path = os.path.join(vault_root, path)
    ownership_handled = False
    if art.get("classification") == "living" and old_fields is not None:
        path, ownership_handled = _maybe_restructure_living_ownership(
            vault_root, router, path, art, old_fields, fields
        )
        abs_path = os.path.join(vault_root, path)
    if old_fields is not None and not ownership_handled:
        new_path = _maybe_rename_on_field_change(vault_root, path, art, old_fields, fields)
        if new_path != path:
            path = new_path
            abs_path = os.path.join(vault_root, path)
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    return _result_payload(path, resolved_path, operation, old_body, new_body)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def edit_artefact(vault_root, router, path, body="", frontmatter_changes=None, target=None):
    """Replace body of existing artefact. Merges frontmatter_changes into existing FM.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        body: New body content. Empty string with no frontmatter changes and no
              target is a no-op error. With frontmatter-only changes, the
              existing body is preserved. Use target=":entire_body" to explicitly
              replace the full body (including clearing it).
        frontmatter_changes: Optional dict of frontmatter field changes (overwrites fields).
        target: Optional heading, callout title, ":entire_body", or ":body_preamble".
                When given, replaces that section or reserved body range with body.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    _validate_edit_request("edit", body, frontmatter_changes, target)
    path, abs_path, fields, existing_body, art = _open_artefact(vault_root, router, path)
    old_fields = dict(fields)
    frontmatter_changes = _normalise_ownership_changes(
        vault_root, router, art, frontmatter_changes
    )
    _merge_frontmatter(fields, frontmatter_changes, "edit")
    ensure_parent_tag(fields)
    new_body = _apply_edit(existing_body, body, target)
    return _finish_artefact(
        vault_root,
        router,
        abs_path,
        fields,
        existing_body,
        new_body,
        path,
        art,
        frontmatter_changes,
        "edit",
        old_fields=old_fields,
    )


def delete_section_artefact(vault_root, router, path, target, frontmatter_changes=None):
    """Delete a section (heading + all its content) from an existing artefact.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        target: Heading or callout title to delete (required).
                Include # markers to disambiguate (e.g. "## Notes").
        frontmatter_changes: Optional dict of frontmatter field changes.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    _validate_edit_request("delete_section", "", frontmatter_changes, target)
    path, abs_path, fields, existing_body, art = _open_artefact(vault_root, router, path)
    old_fields = dict(fields)
    frontmatter_changes = _normalise_ownership_changes(
        vault_root, router, art, frontmatter_changes
    )
    _merge_frontmatter(fields, frontmatter_changes, "edit")
    ensure_parent_tag(fields)
    new_body = _apply_delete_section(existing_body, target)
    return _finish_artefact(
        vault_root,
        router,
        abs_path,
        fields,
        existing_body,
        new_body,
        path,
        art,
        frontmatter_changes,
        "delete_section",
        old_fields=old_fields,
    )


def append_to_artefact(vault_root, router, path, content="", frontmatter_changes=None, target=None):
    """Append content to existing artefact body.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        content: Content to append. Empty string for frontmatter-only changes.
        frontmatter_changes: Optional dict of frontmatter field changes
                             (extends list fields with dedup, overwrites scalars).
        target: Optional heading or callout title. When given, appends at the end of
                that section. Use ":entire_body" to append to the whole body explicitly.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    _validate_edit_request("append", content, frontmatter_changes, target)
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)
    old_fields = dict(fields)
    frontmatter_changes = _normalise_ownership_changes(
        vault_root, router, art, frontmatter_changes
    )
    _merge_frontmatter(fields, frontmatter_changes, "append")
    ensure_parent_tag(fields)
    new_body = _apply_append(body, content, target)
    return _finish_artefact(
        vault_root,
        router,
        abs_path,
        fields,
        body,
        new_body,
        path,
        art,
        frontmatter_changes,
        "append",
        old_fields=old_fields,
    )


def prepend_to_artefact(vault_root, router, path, content="", frontmatter_changes=None, target=None):
    """Prepend content to existing artefact body.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        content: Content to prepend. Empty string for frontmatter-only changes.
        frontmatter_changes: Optional dict of frontmatter field changes
                             (extends list fields with dedup, overwrites scalars).
        target: Optional heading or callout title. When given, inserts content before
                that section's heading line. Use ":entire_body" to prepend to the
                whole body explicitly.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    _validate_edit_request("prepend", content, frontmatter_changes, target)
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)
    old_fields = dict(fields)
    frontmatter_changes = _normalise_ownership_changes(
        vault_root, router, art, frontmatter_changes
    )
    _merge_frontmatter(fields, frontmatter_changes, "prepend")
    ensure_parent_tag(fields)
    new_body = _apply_prepend(body, content, target)
    return _finish_artefact(
        vault_root,
        router,
        abs_path,
        fields,
        body,
        new_body,
        path,
        art,
        frontmatter_changes,
        "prepend",
        old_fields=old_fields,
    )


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------

def convert_artefact(vault_root, router, path, target_type, parent=None):
    """Convert artefact to a different type: move to target folder, reconcile FM, update wikilinks.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        target_type: Target type key or full type (e.g. "design" or "living/design").
        parent: Optional canonical parent artefact reference. If omitted, an existing
                parent is preserved when the target contract permits it. Temporal targets
                keep their normal date-based folders.

    Returns:
        Dict with old_path, new_path, type, and links_updated.

    Raises:
        ValueError: If source or target type resolution fails.
        FileNotFoundError: If the source file does not exist.
    """
    vault_root = str(vault_root)

    path, source_art = resolve_and_validate_folder(vault_root, router, path)

    if is_archived_path(path):
        raise ValueError(
            f"Cannot convert archived file '{path}'. "
            f"Un-archive it first by moving it out of _Archive/."
        )

    target_art = resolve_type(router, target_type)
    abs_source = os.path.join(vault_root, path)
    if not os.path.isfile(abs_source):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_source, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)
    title = _derive_title_from_path(source_art, fields, path)

    source_prefix = artefact_type_prefix(source_art)
    target_prefix = artefact_type_prefix(target_art)
    source_slug = fields.get("key")
    old_key = (
        make_artefact_key(source_prefix, source_slug)
        if source_art.get("classification") == "living" and is_valid_key(source_slug)
        else None
    )
    old_parent = normalize_artefact_key(fields.get("parent"))

    if target_art.get("classification") == "living":
        target_key = _choose_living_key(
            vault_root,
            router,
            target_art,
            title,
            key=source_slug if is_valid_key(source_slug) else None,
            exclude_path=path,
        )
        target_parent = None
        if parent is not None:
            target_parent, _parent_entry = resolve_parent_reference(
                vault_root, router, parent
            )
        elif old_parent:
            target_parent = old_parent

        fields["key"] = target_key
        if target_parent:
            fields["parent"] = target_parent
            ensure_parent_tag(fields)
        else:
            fields.pop("parent", None)
        new_key = make_artefact_key(target_prefix, target_key)
    else:
        fields.pop("key", None)
        if parent is not None:
            target_parent, _parent_entry = resolve_parent_reference(
                vault_root, router, parent
            )
        else:
            target_parent = old_parent
        if target_parent:
            fields["parent"] = target_parent
            ensure_parent_tag(fields)
        else:
            fields.pop("parent", None)
        new_key = None

    if source_art.get("frontmatter_type"):
        fields["type"] = target_art.get("frontmatter_type", target_art["type"])

    if old_key and old_key != new_key:
        if source_prefix in SELF_TAG_PREFIXES:
            replacement = new_key if new_key and target_prefix in SELF_TAG_PREFIXES else None
            _replace_exact_tag(fields, old_key, replacement)
        _apply_reference_mutation(vault_root, router, old_key, new_key, skip_paths={path})
    elif new_key:
        ensure_self_tag(fields, target_prefix, target_key)

    rendered_fields = dict(fields)
    reconcile_fields_for_render(
        rendered_fields, target_art, abs_source, os.path.basename(path)
    )
    target_folder = resolve_folder(
        target_art,
        parent=normalize_artefact_key(rendered_fields.get("parent")),
        fields=rendered_fields,
        router=router,
    )
    target_basename = render_filename_or_default(
        target_art.get("naming"), title, rendered_fields
    )
    new_path = os.path.join(target_folder, target_basename)
    if new_path != path:
        stem, ext = os.path.splitext(os.path.basename(new_path))
        folder = os.path.dirname(new_path)
        target_abs_folder = os.path.join(vault_root, folder)
        unique_name = unique_filename(target_abs_folder, stem, ext or ".md")
        new_path = os.path.join(folder, unique_name)
    check_write_allowed(new_path)
    _preflight_destination(vault_root, path, new_path)

    safe_write(
        abs_source,
        serialize_frontmatter(rendered_fields, body=body),
        bounds=vault_root,
    )
    links_updated = 0
    if new_path != path:
        links_updated = rename_and_update_links(vault_root, path, new_path)

    return {
        "old_path": path,
        "new_path": new_path,
        "type": target_art["type"],
        "links_updated": links_updated,
    }


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------

_DATE_PREFIX_RE = re.compile(r"^\d{8}-")


def archive_artefact(vault_root, router, path):
    """Archive a living artefact to the top-level _Archive/ directory.

    1. Resolve path, read frontmatter, validate type has terminal statuses.
    2. Validate current status is terminal (caller must set it first).
    3. Add archiveddate if not present.
    4. Prepend yyyymmdd- date prefix to filename if not present.
    5. Move to _Archive/{type_folder}/{project}/.

    Returns dict with old_path, new_path, links_updated.
    """
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)
    vault_root = str(vault_root)

    if is_archived_path(path):
        raise ValueError(f"'{path}' is already archived.")

    terminal = art.get("frontmatter", {}).get("terminal_statuses") or []
    if not terminal:
        raise ValueError(
            f"Type '{art['type']}' has no terminal statuses — cannot archive."
        )

    status = fields.get("status", "")
    if status not in terminal:
        raise ValueError(
            f"Cannot archive '{path}': status '{status}' is not terminal. "
            f"Terminal statuses for {art['type']}: {', '.join(terminal)}"
        )

    today = now_iso()[:10]
    if "archiveddate" not in fields:
        fields["archiveddate"] = today

    filename = os.path.basename(path)
    date_prefix = today.replace("-", "")
    if not _DATE_PREFIX_RE.match(filename):
        filename = f"{date_prefix}-{filename}"

    type_folder = art["path"]
    rel_from_type = os.path.relpath(os.path.dirname(path), type_folder)
    # Strip +Status/ folders from the path (archived files don't need them)
    parts = rel_from_type.split(os.sep)
    parts = [p for p in parts if not p.startswith("+")]
    rel_from_type = os.path.join(*parts) if parts and parts != ["."] else "."

    if rel_from_type == ".":
        dest = os.path.join("_Archive", type_folder, filename)
    else:
        dest = os.path.join("_Archive", type_folder, rel_from_type, filename)

    _save_artefact(abs_path, fields, body, vault_root)
    links_updated = rename_and_update_links(vault_root, path, dest)

    return {
        "old_path": path,
        "new_path": dest,
        "links_updated": links_updated,
    }


def unarchive_artefact(vault_root, router, path):
    """Restore an archived artefact from _Archive/ to its original type folder.

    1. Validate path is in _Archive/.
    2. Strip yyyymmdd- date prefix from filename.
    3. Compute original type folder destination.
    4. Remove archiveddate from frontmatter.
    5. Move via rename_and_update_links.

    Returns dict with old_path, new_path, links_updated.
    """
    vault_root = str(vault_root)

    if not is_archived_path(path):
        raise ValueError(f"'{path}' is not in _Archive/.")

    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)

    filename = os.path.basename(path)
    if _DATE_PREFIX_RE.match(filename):
        filename = _DATE_PREFIX_RE.sub("", filename)

    # _Archive/Ideas/Brain/20260101-old-idea.md → Ideas/Brain/old-idea.md
    rel_from_archive = os.path.relpath(os.path.dirname(path), "_Archive")
    dest = os.path.join(rel_from_archive, filename)
    check_write_allowed(dest)

    fields.pop("archiveddate", None)
    _save_artefact(abs_path, fields, body, vault_root)
    links_updated = rename_and_update_links(vault_root, path, dest)

    return {
        "old_path": path,
        "new_path": dest,
        "links_updated": links_updated,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    operation = None
    path = None
    body = ""
    body_file_path = ""
    vault_arg = None
    json_mode = False
    fm_json = None
    target = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--path" and i + 1 < len(sys.argv):
            path = sys.argv[i + 1]
            i += 2
        elif arg == "--body" and i + 1 < len(sys.argv):
            body = sys.argv[i + 1]
            i += 2
        elif arg == "--body-file" and i + 1 < len(sys.argv):
            body_file_path = sys.argv[i + 1]
            i += 2
        elif arg == "--frontmatter" and i + 1 < len(sys.argv):
            fm_json = sys.argv[i + 1]
            i += 2
        elif arg == "--target" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
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
        elif not arg.startswith("--") and operation is None:
            operation = arg
            i += 1
        else:
            i += 1

    if operation not in ("edit", "append", "prepend", "delete_section") or not path:
        print(
            'Usage: edit.py edit|append|prepend|delete_section --path PATH --target HEADING [--vault PATH] [--json] [--temp-path [SUFFIX]]',
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        body, _ = resolve_body_file(body, body_file_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))

    router = load_compiled_router(vault_root)
    if "error" in router:
        if json_mode:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    fm_changes = json.loads(fm_json) if fm_json else None

    try:
        if operation == "edit":
            result = edit_artefact(vault_root, router, path, body, frontmatter_changes=fm_changes, target=target)
        elif operation == "append":
            result = append_to_artefact(vault_root, router, path, body, frontmatter_changes=fm_changes, target=target)
        elif operation == "prepend":
            result = prepend_to_artefact(vault_root, router, path, body, frontmatter_changes=fm_changes, target=target)
        else:
            result = delete_section_artefact(vault_root, router, path, target=target, frontmatter_changes=fm_changes)
    except (ValueError, FileNotFoundError) as e:
        if json_mode:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        op_label = OPERATION_LABELS[operation]
        print(f"{op_label} {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
