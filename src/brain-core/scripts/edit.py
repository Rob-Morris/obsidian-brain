#!/usr/bin/env python3
"""
edit.py — Edit or append to an existing vault artefact.

Validates paths against the compiled router, then modifies file content
with frontmatter preservation. Also provides artefact type conversion.

Usage:
    python3 edit.py edit --path "Wiki/my-page.md" --body "New body"
    python3 edit.py append --path "Wiki/my-page.md" --body "Appended text"
    python3 edit.py edit --path "Wiki/my-page.md" --body "New body" --vault /path --json
"""

import json
import os
import sys

from check import naming_pattern_to_regex
from create import resolve_naming_pattern, resolve_type, resolve_folder

from _common import (
    build_wikilink_pattern,
    find_vault_root,
    parse_frontmatter,
    replace_wikilinks_in_vault,
    serialize_frontmatter,
    strip_md_ext,
    title_to_slug,
)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def validate_artefact_path(vault_root, router, path):
    """Validate path belongs to a known type folder. Returns artefact dict or raises ValueError."""
    vault_root = str(vault_root)

    for art in router.get("artefacts", []):
        art_path = art["path"]
        # Check if the given path starts with the artefact's folder path
        if path.startswith(art_path + os.sep) or path.startswith(art_path + "/"):
            if not art.get("configured"):
                raise ValueError(
                    f"Path '{path}' belongs to unconfigured type '{art['key']}'. "
                    f"Create a taxonomy file first."
                )

            # Optionally check naming pattern
            naming = art.get("naming")
            if naming and naming.get("pattern"):
                regex = naming_pattern_to_regex(naming["pattern"])
                if regex:
                    filename = os.path.basename(path)
                    if not regex.match(filename):
                        raise ValueError(
                            f"Filename '{filename}' does not match expected pattern "
                            f"'{naming['pattern']}' for type '{art['key']}'"
                        )

            return art

    known_paths = [a["path"] for a in router.get("artefacts", [])]
    raise ValueError(
        f"Path '{path}' does not belong to any known artefact folder. "
        f"Known: {', '.join(known_paths)}"
    )


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def edit_artefact(vault_root, router, path, body, frontmatter_changes=None):
    """Replace body of existing artefact. Merges frontmatter_changes into existing FM.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        body: New body content (replaces existing body).
        frontmatter_changes: Optional dict of frontmatter field changes.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails.
        FileNotFoundError: If the file does not exist.
    """
    vault_root = str(vault_root)
    validate_artefact_path(vault_root, router, path)

    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()

    fields, _ = parse_frontmatter(content)

    # Merge frontmatter changes
    if frontmatter_changes:
        fields.update(frontmatter_changes)

    # Write back with new body
    new_content = serialize_frontmatter(fields, body=body)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return {"path": path, "operation": "edit"}


def append_to_artefact(vault_root, router, path, content):
    """Append content to existing artefact body.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        content: Content to append.

    Returns:
        Dict with path and operation.

    Raises:
        ValueError: If path validation fails.
        FileNotFoundError: If the file does not exist.
    """
    vault_root = str(vault_root)
    validate_artefact_path(vault_root, router, path)

    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        existing = f.read()

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(existing + content)

    return {"path": path, "operation": "append"}


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------

def convert_artefact(vault_root, router, path, target_type):
    """Convert artefact to a different type: move to target folder, reconcile FM, update wikilinks.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        target_type: Target type key or full type (e.g. "design" or "living/design").

    Returns:
        Dict with old_path, new_path, type, and links_updated.

    Raises:
        ValueError: If source or target type resolution fails.
        FileNotFoundError: If the source file does not exist.
    """
    vault_root = str(vault_root)

    # Validate source
    abs_source = os.path.join(vault_root, path)
    if not os.path.isfile(abs_source):
        raise FileNotFoundError(f"File not found: {path}")

    # Resolve source and target artefact types
    validate_artefact_path(vault_root, router, path)
    target_art = resolve_type(router, target_type)

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

    # Compute new path
    target_folder = resolve_folder(target_art)
    new_path = os.path.join(target_folder, new_filename)

    # Reconcile frontmatter: set type to target type
    if target_art.get("frontmatter") and target_art["frontmatter"].get("type"):
        fields["type"] = target_art["frontmatter"]["type"]

    # Write updated content to new path
    abs_new = os.path.join(vault_root, new_path)
    os.makedirs(os.path.dirname(abs_new), exist_ok=True)
    new_content = serialize_frontmatter(fields, body=body)
    with open(abs_new, "w", encoding="utf-8") as f:
        f.write(new_content)

    # Update wikilinks vault-wide (old stem → new stem)
    old_stem = strip_md_ext(path)
    new_stem = strip_md_ext(new_path)
    pattern = build_wikilink_pattern(old_stem)
    links_updated = replace_wikilinks_in_vault(
        vault_root, pattern,
        lambda m: f"[[{new_stem}{m.group(1) or ''}]]",
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
    vault_arg = None
    json_mode = False
    fm_json = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--path" and i + 1 < len(sys.argv):
            path = sys.argv[i + 1]
            i += 2
        elif arg == "--body" and i + 1 < len(sys.argv):
            body = sys.argv[i + 1]
            i += 2
        elif arg == "--frontmatter" and i + 1 < len(sys.argv):
            fm_json = sys.argv[i + 1]
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

    if operation not in ("edit", "append") or not path:
        print(
            'Usage: edit.py edit|append --path PATH --body BODY [--vault PATH] [--json]',
            file=sys.stderr,
        )
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
            result = edit_artefact(vault_root, router, path, body, frontmatter_changes=fm_changes)
        else:
            result = append_to_artefact(vault_root, router, path, body)
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
