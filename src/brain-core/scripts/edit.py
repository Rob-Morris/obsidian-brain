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
import sys

from check import (
    naming_pattern_to_regex,
    resolve_and_validate_folder,
    validate_artefact_folder,
    validate_artefact_naming,
    validate_artefact_path,
)
from create import resolve_naming_pattern, resolve_type, resolve_folder

from _common import (
    find_section,
    find_vault_root,
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
# Shared helpers
# ---------------------------------------------------------------------------

def _open_artefact(vault_root, router, path):
    """Validate, read, and parse an artefact. Returns (path, abs_path, fields, body)."""
    vault_root = str(vault_root)
    path, _ = resolve_and_validate_folder(vault_root, router, path)
    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)
    return path, abs_path, fields, body


def _merge_frontmatter(fields, changes, operation):
    """Merge frontmatter changes using operation-appropriate strategy.

    edit: overwrite all fields (set semantics).
    append/prepend: extend list fields with dedup, overwrite scalars.
    """
    if not changes:
        return
    for key, value in changes.items():
        if operation != "edit" and isinstance(value, list) and isinstance(fields.get(key), list):
            fields[key].extend(v for v in value if v not in fields[key])
        else:
            fields[key] = value


def _save_artefact(abs_path, fields, new_body, vault_root):
    """Set modified timestamp, serialize, and write."""
    fields["modified"] = now_iso()
    new_content = serialize_frontmatter(fields, body=new_body)
    safe_write(abs_path, new_content, bounds=vault_root)


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
    path, abs_path, fields, existing_body = _open_artefact(vault_root, router, path)
    _merge_frontmatter(fields, frontmatter_changes, "edit")

    if target == ":body":
        # Explicit whole-body replacement (including clearing with body="")
        new_body = body
    elif target:
        start, end = find_section(existing_body, target)
        # Ensure clean join: body ends with newline, blank line before next section
        normalized = body.rstrip("\n") + "\n"
        if end < len(existing_body):
            normalized += "\n"  # blank line before next heading
        new_body = existing_body[:start] + normalized + existing_body[end:]
    elif body:
        new_body = body
    else:
        new_body = existing_body

    _save_artefact(abs_path, fields, new_body, vault_root)
    return {"path": path, "operation": "edit"}


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
    path, abs_path, fields, body = _open_artefact(vault_root, router, path)
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
    return {"path": path, "operation": "append"}


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
    path, abs_path, fields, body = _open_artefact(vault_root, router, path)
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
    return {"path": path, "operation": "prepend"}


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

    if operation not in ("edit", "append", "prepend") or not path:
        print(
            'Usage: edit.py edit|append|prepend --path PATH --body BODY [--body-file PATH] [--target HEADING] [--vault PATH] [--json]',
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
        else:
            result = prepend_to_artefact(vault_root, router, path, body, frontmatter_changes=fm_changes, target=target)
    except (ValueError, FileNotFoundError) as e:
        if json_mode:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        print(f"{operation.capitalize()}ed {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
