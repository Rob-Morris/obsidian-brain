#!/usr/bin/env python3
"""
edit.py — Edit, append, or prepend to an existing vault artefact.

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
from create import resolve_naming_pattern, resolve_type, resolve_folder

from rename import rename_and_update_links

from _common import (
    find_section,
    find_vault_root,
    is_archived_path,
    make_wikilink_replacer,
    now_iso,
    parse_frontmatter,
    replace_wikilinks_in_vault,
    resolve_body_file,
    resolve_wikilink_stems,
    safe_write,
    serialize_frontmatter,
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
        new_path = os.path.join(parent_dir, status_folder, filename)
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

    if target == ":body":
        # Explicit whole-body replacement (including clearing with body="")
        new_body = body
    elif target:
        start, end = find_section(existing_body, target)
        # Ensure clean join: body ends with newline, blank line before next section
        if body:
            normalized = body.rstrip("\n") + "\n"
            if end < len(existing_body):
                normalized += "\n"  # blank line before next heading
        else:
            normalized = "" if end == len(existing_body) else "\n"
        new_body = existing_body[:start] + normalized + existing_body[end:]
    elif body:
        new_body = body
    else:
        new_body = existing_body

    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    return {"path": path, "resolved_path": resolved_path, "operation": "edit"}


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

    start, end = find_section(existing_body, target, include_heading=True)

    # Strip trailing blank lines from prefix so deletion doesn't leave double-blanks
    prefix = existing_body[:start].rstrip("\n")
    suffix = existing_body[end:]

    if prefix and suffix:
        new_body = prefix + "\n\n" + suffix
    elif prefix:
        new_body = prefix + "\n"
    else:
        new_body = suffix

    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    return {"path": path, "resolved_path": resolved_path, "operation": "delete_section"}


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

    if target and target != ":body" and content:
        section_start, section_end = find_section(body, target)
        section_body = body[section_start:section_end].rstrip("\n")
        content_normalized = content if content.endswith("\n") else content + "\n"
        if section_body:
            rebuilt = section_body + "\n" + content_normalized
        else:
            rebuilt = "\n" + content_normalized
        if section_end < len(body):
            rebuilt += "\n"  # blank line before next heading
        new_body = body[:section_start] + rebuilt + body[section_end:]
    elif content:
        new_body = body + content
    else:
        new_body = body

    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    return {"path": path, "resolved_path": resolved_path, "operation": "append"}


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

    if target and target != ":body" and content:
        heading_start, _ = find_section(body, target, include_heading=True)
        content_normalized = content.rstrip("\n") + "\n"
        if heading_start > 0:
            content_normalized += "\n"  # blank line between content and heading
        new_body = body[:heading_start] + content_normalized + body[heading_start:]
    elif content:
        content_normalized = content if content.endswith("\n") else content + "\n"
        new_body = content_normalized + ("\n" if body else "") + body
    else:
        new_body = body

    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    return {"path": path, "resolved_path": resolved_path, "operation": "prepend"}


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
        elif not arg.startswith("--") and operation is None:
            operation = arg
            i += 1
        else:
            i += 1

    if operation not in ("edit", "append", "prepend", "delete_section") or not path:
        print(
            'Usage: edit.py edit|append|prepend|delete_section --path PATH --target HEADING [--vault PATH] [--json]',
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
