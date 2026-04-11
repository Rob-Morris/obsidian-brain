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

from check import (
    naming_pattern_to_regex,
    resolve_and_validate_folder,
    validate_artefact_folder,
    validate_artefact_naming,
    validate_artefact_path,
)
from create import config_resource_rel_path, resolve_naming_pattern, resolve_type, resolve_folder

from rename import rename_and_update_links

from _common import (
    check_write_allowed,
    find_section,
    find_vault_root,
    is_archived_path,
    make_wikilink_replacer,
    make_temp_path,
    now_iso,
    parse_frontmatter,
    replace_wikilinks_in_vault,
    resolve_body_file,
    resolve_wikilink_stems,
    safe_write,
    serialize_frontmatter,
    parse_structural_anchor_line,
    strip_md_ext,
    title_to_slug,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPERATION_LABELS = {
    "edit": "Edited",
    "append": "Appended",
    "prepend": "Prepended",
    "delete_section": "Deleted section from",
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

def _apply_edit(existing_body, body, target):
    """Apply an edit operation to a body, returning the new body."""
    if target == ":body":
        return body
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
        if body:
            normalized = body.rstrip("\n") + "\n"
            if end < len(existing_body):
                normalized += "\n"
        else:
            normalized = "" if end == len(existing_body) else "\n"
        return existing_body[:start] + normalized + existing_body[end:]
    if body:
        return body
    return existing_body


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


def _apply_append(existing_body, content, target):
    """Apply an append operation to a body, returning the new body."""
    if target and target.startswith(":section:"):
        target = target[len(":section:"):].strip()
    if target and target != ":body" and content:
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
    if target and target.startswith(":section:"):
        target = target[len(":section:"):].strip()
    if target and target != ":body" and content:
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
                  target=None):
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
        target: Optional heading/callout for targeted operations.

    Returns:
        Dict with path and operation.
    """
    vault_root = str(vault_root)

    if resource == "artefact":
        if not path:
            raise ValueError("path is required when resource='artefact'")
        if operation == "edit":
            return edit_artefact(vault_root, router, path, body,
                                frontmatter_changes=frontmatter_changes, target=target)
        elif operation == "append":
            return append_to_artefact(vault_root, router, path, body,
                                     frontmatter_changes=frontmatter_changes, target=target)
        elif operation == "prepend":
            return prepend_to_artefact(vault_root, router, path, body,
                                      frontmatter_changes=frontmatter_changes, target=target)
        elif operation == "delete_section":
            return delete_section_artefact(vault_root, router, path,
                                          target=target, frontmatter_changes=frontmatter_changes)
        else:
            raise ValueError(f"Unknown operation '{operation}'")

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

    fm_mode = "edit" if operation in ("edit", "delete_section") else operation
    _merge_frontmatter(fields, frontmatter_changes, fm_mode)

    if operation == "delete_section":
        new_body = apply_fn(existing_body, target)
    else:
        new_body = apply_fn(existing_body, body, target)

    # Save without artefact-specific behavior (no modified auto-set, no status move)
    new_content = serialize_frontmatter(fields, body=new_body)
    safe_write(abs_path, new_content, bounds=vault_root)

    return {"path": rel_path, "resolved_path": rel_path, "operation": operation}


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


def _finish_artefact(vault_root, abs_path, fields, new_body, path, art, frontmatter_changes, operation):
    """Save artefact, handle terminal-status auto-move, return result dict."""
    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    return {"path": path, "resolved_path": resolved_path, "operation": operation}


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def edit_artefact(vault_root, router, path, body="", frontmatter_changes=None, target=None):
    """Replace body of existing artefact. Merges frontmatter_changes into existing FM.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        body: New body content. Empty string with no target preserves existing body.
              Use target=":body" to explicitly set body content (including clearing it).
        frontmatter_changes: Optional dict of frontmatter field changes (overwrites fields).
        target: Optional heading, callout title, or ":body" to target the whole document body.
                When given, replaces that section's content with body parameter.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    path, abs_path, fields, existing_body, art = _open_artefact(vault_root, router, path)
    _merge_frontmatter(fields, frontmatter_changes, "edit")
    new_body = _apply_edit(existing_body, body, target)
    return _finish_artefact(vault_root, abs_path, fields, new_body, path, art, frontmatter_changes, "edit")


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
    path, abs_path, fields, existing_body, art = _open_artefact(vault_root, router, path)
    _merge_frontmatter(fields, frontmatter_changes, "edit")
    new_body = _apply_delete_section(existing_body, target)
    return _finish_artefact(vault_root, abs_path, fields, new_body, path, art, frontmatter_changes, "delete_section")


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
                that section. ":body" is treated as no target (append to whole body).

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)
    _merge_frontmatter(fields, frontmatter_changes, "append")
    new_body = _apply_append(body, content, target)
    return _finish_artefact(vault_root, abs_path, fields, new_body, path, art, frontmatter_changes, "append")


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
                that section's heading line. ":body" is treated as no target
                (prepend to whole body).

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails or target not found.
        FileNotFoundError: If the file does not exist.
    """
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)
    _merge_frontmatter(fields, frontmatter_changes, "prepend")
    new_body = _apply_prepend(body, content, target)
    return _finish_artefact(vault_root, abs_path, fields, new_body, path, art, frontmatter_changes, "prepend")


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
        parent: Optional hub subfolder override (e.g. "Brain"). If omitted, the parent
                subfolder is auto-detected from the source path for living target types.

    Returns:
        Dict with old_path, new_path, type, and links_updated.

    Raises:
        ValueError: If source or target type resolution fails.
        FileNotFoundError: If the source file does not exist.
    """
    vault_root = str(vault_root)

    # Resolve path (basename fallback) and validate
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

    # Read and parse source file
    with open(abs_source, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)

    # Compute new filename from target naming pattern
    title = fields.get("title") or os.path.splitext(os.path.basename(path))[0]
    target_naming = target_art.get("naming")
    if target_naming and target_naming.get("pattern"):
        new_filename = resolve_naming_pattern(target_naming["pattern"], title)
    else:
        new_filename = title_to_slug(title) + ".md"

    if parent is None and target_art.get("classification") != "temporal":
        source_dir = os.path.dirname(path)
        rel = os.path.relpath(source_dir, source_art["path"])
        parent = rel if rel != "." else None

    # Compute new path
    target_folder = resolve_folder(target_art, parent=parent)
    new_path = os.path.join(target_folder, new_filename)
    check_write_allowed(new_path)

    # Reconcile frontmatter: set type to target type
    if target_art.get("frontmatter_type"):
        fields["type"] = target_art["frontmatter_type"]

    # Write updated content to new path
    abs_new = os.path.join(vault_root, new_path)
    new_content = serialize_frontmatter(fields, body=body)
    safe_write(abs_new, new_content, bounds=vault_root)

    # Update wikilinks vault-wide (old stem → new stem)
    pattern, stem_map = resolve_wikilink_stems(vault_root, path, new_path)
    links_updated = replace_wikilinks_in_vault(
        vault_root, pattern, make_wikilink_replacer(stem_map),
    )

    # Remove old file
    os.remove(abs_source)

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

    from check import load_router
    router = load_router(vault_root)
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
