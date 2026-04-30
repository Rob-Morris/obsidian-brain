#!/usr/bin/env python3
"""
rename.py — Rename or delete a vault file and update all wikilinks.

Scans all .md files in the vault for wikilinks matching the old path stem,
replaces them with the new path stem (rename) or strikethrough text (delete),
then renames/removes the file itself.

Usage:
    python3 rename.py "Wiki/old-name.md" "Wiki/new-name.md"
    python3 rename.py --vault /path/to/vault "source.md" "dest.md"
    python3 rename.py "source.md" "dest.md" --json
"""

import json
import os
import sys

from _common import (
    check_write_allowed,
    check_not_in_brain_core,
    find_vault_root,
    is_archived_path,
    load_compiled_router,
    make_wikilink_replacer,
    parse_frontmatter,
    replace_wikilinks_in_vault,
    resolve_and_check_bounds,
    resolve_wikilink_stems,
    validate_artefact_folder,
    validate_filename,
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def validate_rename_request(
    vault_root,
    source,
    dest,
    router=None,
    *,
    allow_archive_paths=False,
):
    """Validate a rename request and return resolved absolute paths."""
    abs_source = os.path.join(vault_root, source)
    abs_dest = os.path.join(vault_root, dest)
    resolve_and_check_bounds(abs_source, vault_root)
    resolve_and_check_bounds(abs_dest, vault_root)
    check_not_in_brain_core(dest, vault_root)
    if not (allow_archive_paths and is_archived_path(dest)):
        check_write_allowed(dest)

    if router is not None:
        _validate_destination_naming(vault_root, router, source, dest, abs_source)

    return abs_source, abs_dest


def rename_and_update_links(
    vault_root,
    source,
    dest,
    router=None,
    *,
    allow_archive_paths=False,
):
    """Rename a file and update wikilinks via grep-and-replace.

    Args:
        vault_root: Absolute path to the vault root.
        source: Relative path from vault root to the source file.
        dest: Relative path from vault root to the destination.
        router: Optional compiled router. When provided, the destination
                filename is validated against the target type's naming
                contract using the source file's current frontmatter state.
                ``_Archive/`` destinations are exempt (they carry an archival
                prefix outside the naming contract).
        allow_archive_paths: Allow internal archive/unarchive flows to move
                into or out of ``_Archive/`` while keeping the default rename
                contract stricter for direct callers.

    Returns:
        Number of wikilinks updated across all files.

    Raises:
        FileNotFoundError: If the source file does not exist.
        ValueError: If the destination filename violates the target type's
                    naming contract.
    """
    abs_source, abs_dest = validate_rename_request(
        vault_root,
        source,
        dest,
        router=router,
        allow_archive_paths=allow_archive_paths,
    )

    if not os.path.isfile(abs_source):
        raise FileNotFoundError(f"Source file not found: {source}")

    if os.path.isfile(abs_dest):
        try:
            same = os.path.samefile(abs_source, abs_dest)
        except OSError:
            same = False
        if not same:
            raise FileExistsError(f"Destination file already exists: {dest}")

    pattern, stem_map = resolve_wikilink_stems(vault_root, source, dest)
    links_updated = replace_wikilinks_in_vault(
        vault_root, pattern, make_wikilink_replacer(stem_map),
    )

    # Create destination directory if needed and rename the file
    os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
    os.rename(abs_source, abs_dest)

    return links_updated


def _validate_destination_naming(vault_root, router, source, dest, abs_source):
    """Validate dest filename against the target type's naming contract.

    Skipped when the destination lives outside any known artefact folder
    (e.g. ``_Archive/``) or the target type has no naming contract.
    """
    if is_archived_path(dest):
        return
    try:
        target_art = validate_artefact_folder(vault_root, router, dest)
    except ValueError:
        return
    naming = target_art.get("naming")
    if not naming:
        return
    try:
        with open(abs_source, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        fields = {}
    else:
        fields, _ = parse_frontmatter(text)
    filename = os.path.basename(dest)
    if not validate_filename(naming, fields or {}, filename):
        raise ValueError(
            f"Destination filename '{filename}' does not match the naming "
            f"contract for type '{target_art['key']}'."
        )


def delete_and_clean_links(vault_root, path):
    """Delete a file and replace wikilinks with strikethrough text.

    [[path|alias]] → ~~alias~~
    [[path]]       → ~~path stem~~

    Args:
        vault_root: Absolute path to the vault root.
        path: Relative path from vault root to the file to delete.

    Returns:
        Number of wikilinks replaced across all files.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    abs_path = os.path.join(vault_root, path)
    resolve_and_check_bounds(abs_path, vault_root)
    check_not_in_brain_core(path, vault_root)
    check_write_allowed(path)
    if is_archived_path(path):
        raise ValueError(
            "Delete does not operate on _Archive/. "
            "Use brain_move(op='unarchive') first or remove the file manually."
        )
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    display_name = os.path.splitext(os.path.basename(path))[0]
    pattern, _stem_map = resolve_wikilink_stems(vault_root, path)

    def replacement(m):
        alias = m.group("alias")
        return f"~~{alias[1:]}~~" if alias else f"~~{display_name}~~"

    links_replaced = replace_wikilinks_in_vault(vault_root, pattern, replacement)

    os.remove(abs_path)
    return links_replaced


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    vault_arg = None
    json_mode = False
    positional = []

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        elif not arg.startswith("--"):
            positional.append(arg)
            i += 1
        else:
            i += 1

    if len(positional) != 2:
        print(
            'Usage: rename.py "source.md" "dest.md" [--vault PATH] [--json]',
            file=sys.stderr,
        )
        sys.exit(1)

    source, dest = positional
    vault_root = str(find_vault_root(vault_arg))

    router = load_compiled_router(vault_root)
    if "error" in router:
        router = None  # rename can still run without a router

    try:
        links_updated = rename_and_update_links(vault_root, source, dest, router=router)
    except (FileNotFoundError, ValueError) as e:
        if json_mode:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps({
            "status": "ok",
            "method": "grep_replace",
            "source": source,
            "dest": dest,
            "links_updated": links_updated,
        }, indent=2))
    else:
        print(f"Renamed {source} → {dest} ({links_updated} links updated)", file=sys.stderr)


if __name__ == "__main__":
    main()
