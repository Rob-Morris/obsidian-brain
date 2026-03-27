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
    find_vault_root,
    replace_wikilinks_in_vault,
    resolve_wikilink_stems,
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def rename_and_update_links(vault_root, source, dest):
    """Rename a file and update wikilinks via grep-and-replace.

    Args:
        vault_root: Absolute path to the vault root.
        source: Relative path from vault root to the source file.
        dest: Relative path from vault root to the destination.

    Returns:
        Number of wikilinks updated across all files.

    Raises:
        FileNotFoundError: If the source file does not exist.
    """
    abs_source = os.path.join(vault_root, source)
    abs_dest = os.path.join(vault_root, dest)

    if not os.path.isfile(abs_source):
        raise FileNotFoundError(f"Source file not found: {source}")

    pattern, stem_map = resolve_wikilink_stems(vault_root, source, dest)
    links_updated = replace_wikilinks_in_vault(
        vault_root, pattern,
        lambda m: f"[[{stem_map[m.group(1)]}{m.group(2) or ''}]]",
    )

    # Create destination directory if needed and rename the file
    os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
    os.rename(abs_source, abs_dest)

    return links_updated


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
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    display_name = os.path.splitext(os.path.basename(path))[0]
    pattern, _stem_map = resolve_wikilink_stems(vault_root, path)

    def replacement(m):
        alias = m.group(2)
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

    try:
        links_updated = rename_and_update_links(vault_root, source, dest)
    except FileNotFoundError as e:
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
