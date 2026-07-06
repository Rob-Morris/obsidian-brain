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
    build_md_basename_counts,
    build_wikilink_pattern,
    canonical_living_artefact_key,
    check_write_allowed,
    check_not_in_brain_core,
    descendant_entries,
    descendant_payload,
    find_vault_root,
    HasDescendantsError,
    PartialApplyError,
    is_archived_path,
    load_compiled_router,
    make_wikilink_replacer,
    parse_frontmatter,
    replace_wikilinks_in_vault,
    resolve_and_check_bounds,
    validate_artefact_folder,
    validate_filename,
    wikilink_stems_for_path_change,
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
    abs_source, abs_dest = validate_move_path_request(
        vault_root,
        source,
        dest,
        allow_archive_paths=allow_archive_paths,
    )

    if router is not None:
        _validate_destination_naming(vault_root, router, source, dest, abs_source)

    return abs_source, abs_dest


def validate_move_path_request(
    vault_root,
    source,
    dest,
    *,
    allow_archive_paths=False,
):
    """Validate path/bounds/write gates for a filesystem move."""
    abs_source = os.path.join(vault_root, source)
    abs_dest = os.path.join(vault_root, dest)
    resolve_and_check_bounds(abs_source, vault_root)
    resolve_and_check_bounds(abs_dest, vault_root)
    check_not_in_brain_core(dest, vault_root)
    if not (allow_archive_paths and is_archived_path(dest)):
        check_write_allowed(dest)
    if os.path.islink(abs_source):
        raise ValueError(f"Move source cannot be a symlink: {source}")
    if os.path.lexists(abs_dest) and os.path.islink(abs_dest):
        raise ValueError(f"Move destination cannot be a symlink: {dest}")

    return abs_source, abs_dest


def _normalise_move(move):
    """Return a normalised ``{"source": ..., "dest": ...}`` move dict."""
    source = move.get("source")
    dest = move.get("dest")
    if not source or not dest:
        raise ValueError("Each move requires source and dest")
    return {"source": source, "dest": dest}


def _same_existing_file(path_a, path_b):
    try:
        return os.path.samefile(path_a, path_b)
    except OSError:
        return False


def _path_identity(abs_path):
    return os.path.realpath(abs_path)


def _vault_relative_identity(vault_root, abs_path):
    vault_identity = _path_identity(vault_root)
    return os.path.relpath(_path_identity(abs_path), vault_identity)


def _validate_destination_parent_directory(vault_root, dest, abs_dest):
    current = os.path.dirname(abs_dest)
    while current and not os.path.lexists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if current and os.path.lexists(current) and not os.path.isdir(current):
        rel_parent = os.path.relpath(current, vault_root)
        raise NotADirectoryError(
            f"Destination parent is not a directory: {rel_parent} for {dest}"
        )


def validate_destination_parent_directory(vault_root, dest, *, allow_archive_paths=False):
    """Raise if an existing destination parent component is not a directory."""
    abs_dest = os.path.join(vault_root, dest)
    resolve_and_check_bounds(abs_dest, vault_root)
    check_not_in_brain_core(dest, vault_root)
    if not (allow_archive_paths and is_archived_path(dest)):
        check_write_allowed(dest)
    if os.path.lexists(abs_dest) and os.path.islink(abs_dest):
        raise ValueError(f"Move destination cannot be a symlink: {dest}")
    _validate_destination_parent_directory(vault_root, dest, abs_dest)


def move_destination_collision(vault_root, source, dest, sources):
    """Return a collision reason for a planned move destination, or None.

    Mirrors the batch move preflight rule: an existing destination is allowed
    when it is the same filesystem object as the source, or when that
    destination is itself vacated by another source in the same move set.
    """
    abs_source = os.path.join(vault_root, source)
    abs_dest = os.path.join(vault_root, dest)
    source_id = _path_identity(abs_source)
    dest_id = _path_identity(abs_dest)
    if source_id == dest_id:
        return None
    if not os.path.exists(abs_dest):
        return None
    if _same_existing_file(abs_source, abs_dest):
        return None
    source_ids = {
        _path_identity(os.path.join(vault_root, planned_source))
        for planned_source in sources
    }
    if dest_id in source_ids:
        return None
    return "destination already exists"


def preflight_move_set(vault_root, moves, router=None, *, allow_archive_paths=False):
    """Validate a batch move set before any link rewrite or filesystem move.

    ``router`` is accepted for compatibility with older callers; path-only
    move safety is independent of router state.
    """
    planned = []
    seen_sources = set()
    seen_dests = set()

    for raw in moves:
        move = _normalise_move(raw)
        source = move["source"]
        dest = move["dest"]
        abs_source, abs_dest = validate_move_path_request(
            vault_root,
            source,
            dest,
            allow_archive_paths=allow_archive_paths,
        )
        if not os.path.isfile(abs_source):
            raise FileNotFoundError(f"Source file not found: {source}")
        _validate_destination_parent_directory(vault_root, dest, abs_dest)
        source_id = _path_identity(abs_source)
        dest_id = _path_identity(abs_dest)
        canonical_source = _vault_relative_identity(vault_root, abs_source)
        canonical_dest = _vault_relative_identity(vault_root, abs_dest)
        if source_id in seen_sources:
            raise ValueError(f"Duplicate move source: {source}")
        if dest_id in seen_dests:
            raise ValueError(f"Duplicate move destination: {dest}")
        seen_sources.add(source_id)
        seen_dests.add(dest_id)
        planned.append({
            "source": canonical_source,
            "dest": canonical_dest,
            "original_source": source,
            "original_dest": dest,
            "abs_source": abs_source,
            "abs_dest": abs_dest,
            "source_id": source_id,
            "dest_id": dest_id,
        })

    sources = {move["source"] for move in planned}
    for move in planned:
        source = move["source"]
        dest = move["dest"]
        if move["source_id"] == move["dest_id"]:
            continue
        collision = move_destination_collision(vault_root, source, dest, sources)
        if collision:
            raise FileExistsError(f"Destination file already exists: {dest}")

    _ordered_moves_for_apply(planned)
    return planned


def preflight_rename_move_set(vault_root, moves, router=None, *, allow_archive_paths=False):
    """Validate a rename-facing move set, including destination naming rules."""
    planned = preflight_move_set(
        vault_root,
        moves,
        router=None,
        allow_archive_paths=allow_archive_paths,
    )
    if router is not None:
        for move in planned:
            _validate_destination_naming(
                vault_root,
                router,
                move["source"],
                move["dest"],
                move["abs_source"],
            )
    return planned


def _ordered_moves_for_apply(planned):
    """Order moves so destinations occupied by another source are vacated first."""
    moves = [
        move for move in planned
        if move.get("source_id", move["source"]) != move.get("dest_id", move["dest"])
    ]
    by_source = {move.get("source_id", move["source"]): move for move in moves}
    visiting = set()
    visited = set()
    ordered = []

    def visit(move):
        source = move.get("source_id", move["source"])
        if source in visited:
            return
        if source in visiting:
            raise ValueError(f"Cyclic move set involving: {move['source']}")
        visiting.add(source)
        blocking = by_source.get(move.get("dest_id", move["dest"]))
        if blocking is not None:
            visit(blocking)
        visiting.remove(source)
        visited.add(source)
        ordered.append(move)

    for move in moves:
        visit(move)
    return ordered


def validate_acyclic_move_set(moves):
    """Raise ValueError if a planned move set contains a destination cycle."""
    planned = [_normalise_move(move) for move in moves]
    _ordered_moves_for_apply(planned)


def _accumulate_wikilink_stems(items, basename_counts, item_stems):
    stems = []
    stem_map = {}
    for item in items:
        move_stems, move_map = item_stems(item, basename_counts)
        for stem in move_stems:
            replacement = move_map[stem]
            existing = stem_map.get(stem)
            if existing is not None and existing != replacement:
                raise ValueError(
                    "Conflicting wikilink replacement for stem "
                    f"{stem!r}: {existing!r} vs {replacement!r}"
                )
            if existing is None:
                stems.append(stem)
                stem_map[stem] = replacement
    if not stems:
        return None, {}
    return build_wikilink_pattern(*stems), stem_map


def _combined_wikilink_plan(planned, basename_counts):
    """Build one wikilink regex and replacement map for the whole move set."""
    def item_stems(move, counts):
        source = move["source"]
        dest = move["dest"]
        if source == dest:
            return [], {}
        stems, stem_map = wikilink_stems_for_path_change(
            source, dest, basename_counts=counts
        )
        original_source = move.get("original_source")
        if original_source and original_source != source:
            original_stems, original_map = wikilink_stems_for_path_change(
                original_source,
                dest,
                basename_counts=counts,
            )
            for stem in original_stems:
                if stem not in stem_map:
                    stems.append(stem)
                    stem_map[stem] = original_map[stem]
        return stems, stem_map

    return _accumulate_wikilink_stems(planned, basename_counts, item_stems)


def _combined_delete_wikilink_plan(paths, basename_counts):
    """Build one wikilink regex and display-name map for deleted files."""
    def item_stems(path, counts):
        return wikilink_stems_for_path_change(
            path, None, basename_counts=counts
        )

    return _accumulate_wikilink_stems(paths, basename_counts, item_stems)


def move_and_update_links(
    vault_root,
    moves,
    router=None,
    *,
    allow_archive_paths=False,
):
    """Execute a batch file move set with one combined wikilink rewrite pass.

    The operation is fail-fast but not transactional: wikilinks are rewritten
    before filesystem moves, and ``replace_wikilinks_in_vault`` skips files it
    cannot read. If a later move fails, the raised error reports which moves
    already committed so an operator can finish or repair the move set.

    ``router`` is accepted for compatibility with older callers; destination
    naming validation belongs in ``preflight_rename_move_set``.
    """
    planned = preflight_move_set(
        vault_root,
        moves,
        router=None,
        allow_archive_paths=allow_archive_paths,
    )
    ordered = _ordered_moves_for_apply(planned)

    basename_counts = build_md_basename_counts(vault_root)
    pattern, stem_map = _combined_wikilink_plan(planned, basename_counts)
    links_updated = 0
    if pattern is not None:
        links_updated = replace_wikilinks_in_vault(
            vault_root, pattern, make_wikilink_replacer(stem_map),
        )

    applied = []
    for move in ordered:
        try:
            dest_dir = os.path.dirname(move["abs_dest"])
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            os.rename(move["abs_source"], move["abs_dest"])
        except OSError as exc:
            raise PartialApplyError(
                "move set partially applied — links already rewritten; "
                f"committed {applied}, failed at {move['source']}->{move['dest']}: {exc}"
            ) from exc
        applied.append({"source": move["source"], "dest": move["dest"]})

    return {
        "moves": [
            {"source": move["source"], "dest": move["dest"]}
            for move in planned
        ],
        "applied": applied,
        "links_updated": links_updated,
    }


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
    if router is not None:
        preflight_rename_move_set(
            vault_root,
            [{"source": source, "dest": dest}],
            router=router,
            allow_archive_paths=allow_archive_paths,
        )
    result = move_and_update_links(
        vault_root,
        [{"source": source, "dest": dest}],
        router=router,
        allow_archive_paths=allow_archive_paths,
    )
    return result["links_updated"]


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
    if not os.path.exists(abs_source):
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


def _living_key_for_delete(vault_root, router, path, abs_path):
    if router is None:
        return None
    art = validate_artefact_folder(vault_root, router, path)
    with open(abs_path, "r", encoding="utf-8") as f:
        fields, _body = parse_frontmatter(f.read())
    return canonical_living_artefact_key(art, fields)


def _preflight_delete_path(vault_root, path):
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
    return abs_path


def delete_and_clean_links(vault_root, path, router=None, recursive=False):
    """Delete a file and replace wikilinks with strikethrough text.

    [[path|alias]] → ~~alias~~
    [[path]]       → ~~path stem~~

    Args:
        vault_root: Absolute path to the vault root.
        path: Relative path from vault root to the file to delete.
        router: Optional compiled router used to enforce living-descendant gates.
                Router-less delete does not gate descendants and is only for
                primitive/leaf deletion paths.
        recursive: When true, delete the living descendant subtree as well.

    Returns:
        Number of wikilinks replaced across all files.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    abs_path = _preflight_delete_path(vault_root, path)
    descendants = []
    source_key = _living_key_for_delete(vault_root, router, path, abs_path)
    if source_key:
        descendants = descendant_entries(router, source_key)
        if descendants and not recursive:
            raise HasDescendantsError("delete", path, descendant_payload(descendants))

    paths = [path] + [entry["path"] for entry in descendants]
    abs_paths = [abs_path]
    for rel_path in paths[1:]:
        abs_paths.append(_preflight_delete_path(vault_root, rel_path))

    basename_counts = build_md_basename_counts(vault_root)
    pattern, stem_map = _combined_delete_wikilink_plan(paths, basename_counts)

    def replacement(m):
        alias = m.group("alias")
        return f"~~{alias[1:]}~~" if alias else f"~~{stem_map[m.group('stem')]}~~"

    links_replaced = replace_wikilinks_in_vault(vault_root, pattern, replacement)

    removed = []
    for rel_path, abs_delete_path in zip(paths, abs_paths):
        try:
            os.remove(abs_delete_path)
        except OSError as exc:
            raise PartialApplyError(
                f"delete set partially applied — links already rewritten; "
                f"removed {removed}, failed at {rel_path}: {exc}"
            ) from exc
        removed.append(rel_path)
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
    except (FileNotFoundError, ValueError, PartialApplyError, OSError) as e:
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
