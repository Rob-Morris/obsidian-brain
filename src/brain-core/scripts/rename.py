#!/usr/bin/env python3
"""Internal rename/delete and wikilink-reconciliation semantics.

Scans all .md files in the vault for wikilinks matching the old path stem,
replaces them with the new path stem (rename) or strikethrough text (delete),
then renames/removes the file itself. Public callers use ``artefact.rename`` or
``artefact.delete`` through ``command.py``; the parser retained here is an
internal maintenance and repository-test entry point.
"""

import argparse
import json
import os
import sys

import upload_attachment as attachment_upload

from _lifecycle.derived_cache_state import load_fresh_compiled_router
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
    make_wikilink_replacer,
    MutationLockError,
    public_mutation_error_message,
    parse_frontmatter,
    prune_vacated_owner_folders,
    replace_wikilinks_in_vault,
    resolve_and_check_bounds,
    validate_artefact_folder,
    validate_filename,
    vault_mutation_lock,
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
    allow_attachment_paths=False,
):
    """Validate path/bounds/write gates for a filesystem move."""
    abs_source = os.path.join(vault_root, source)
    abs_dest = os.path.join(vault_root, dest)
    resolve_and_check_bounds(abs_source, vault_root)
    resolve_and_check_bounds(abs_dest, vault_root)
    check_not_in_brain_core(dest, vault_root)
    attachment_move = (
        allow_attachment_paths
        and _is_scoped_attachment_path(source)
        and _is_scoped_attachment_path(dest)
    )
    if not attachment_move and not (allow_archive_paths and is_archived_path(dest)):
        check_write_allowed(dest)
    if os.path.islink(abs_source):
        raise ValueError(f"Move source cannot be a symlink: {source}")
    if os.path.lexists(abs_dest) and os.path.islink(abs_dest):
        raise ValueError(f"Move destination cannot be a symlink: {dest}")

    return abs_source, abs_dest


def _is_scoped_attachment_path(path):
    parts = str(path).replace("\\", "/").split("/")
    return (
        len(parts) >= 4
        and parts[0] == "_Assets"
        and parts[1] == "Attachments"
        and parts[2] not in {"", ".", ".."}
        and all(part not in {"", ".", ".."} for part in parts[3:])
    )


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


def move_destination_collision(vault_root, source, dest, sources, *, source_ids=None):
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
    if source_ids is None:
        source_ids = {
            _path_identity(os.path.join(vault_root, planned_source))
            for planned_source in sources
        }
    if dest_id in source_ids:
        return None
    return "destination already exists"


def preflight_move_set(
    vault_root,
    moves,
    *,
    allow_archive_paths=False,
    allow_attachment_paths=False,
):
    """Validate a batch move set before any link rewrite or filesystem move.

    Path-only move safety is independent of router state.
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
            allow_attachment_paths=allow_attachment_paths,
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
    source_ids = {move["source_id"] for move in planned}
    for move in planned:
        source = move["source"]
        dest = move["dest"]
        if move["source_id"] == move["dest_id"]:
            continue
        collision = move_destination_collision(
            vault_root, source, dest, sources, source_ids=source_ids
        )
        if collision:
            raise FileExistsError(f"Destination file already exists: {dest}")

    _ordered_moves_for_apply(planned)
    return planned


def preflight_rename_move_set(
    vault_root,
    moves,
    router=None,
    *,
    allow_archive_paths=False,
):
    """Validate a rename-facing move set, including destination naming rules."""
    planned = preflight_move_set(
        vault_root,
        moves,
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
    *,
    allow_archive_paths=False,
    allow_attachment_paths=False,
    prune_router=None,
):
    """Execute a batch file move set with one combined wikilink rewrite pass.

    The operation is fail-fast but not transactional: wikilinks are rewritten
    before filesystem moves, and ``replace_wikilinks_in_vault`` skips files it
    cannot read. If a later move fails, the raised error reports which moves
    already committed so an operator can finish or repair the move set.

    Pruning is opt-in: when ``prune_router`` is given, the vacated source
    directories are pruned through ``prune_vacated_owner_folders`` once every
    move has committed — ``rmdir``-only, bounded to artefact territory (type
    roots and ``_Archive``), so attachment scopes under ``_Assets`` are never
    touched. It never runs after a ``PartialApplyError``. Across this module
    ``router`` means validation or gating only and ``prune_router`` means
    prune: ``rename_and_update_links`` callers such as ``migrate_to_0_31_0``
    pass ``router=`` for naming validation and must not start pruning.
    """
    planned = preflight_move_set(
        vault_root,
        moves,
        allow_archive_paths=allow_archive_paths,
        allow_attachment_paths=allow_attachment_paths,
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

    if prune_router is not None:
        prune_vacated_owner_folders(
            vault_root, [move["source"] for move in applied], prune_router
        )

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
    prune_router=None,
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
                prefix outside the naming contract). Passing ``router`` does
                *not* prune; see ``prune_router``.
        allow_archive_paths: Allow internal archive/unarchive flows to move
                into or out of ``_Archive/`` while keeping the default rename
                contract stricter for direct callers.
        prune_router: Optional compiled router that opts in to pruning the
                vacated source directory after the move (see
                ``move_and_update_links``).

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
        allow_archive_paths=allow_archive_paths,
        prune_router=prune_router,
    )
    return result["links_updated"]


def rename_artefact(vault_root, router, source, dest):
    """Rename within one configured type while preserving wikilinks.

    Type conversion is deliberately excluded from this semantic operation.
    Callers that need to change type must use ``edit.convert_artefact``.
    """
    if is_archived_path(source) or is_archived_path(dest):
        raise ValueError(
            "Rename cannot target _Archive/. Use the archive or unarchive "
            "operation for archive transitions."
        )
    source_art = validate_artefact_folder(vault_root, router, source)
    dest_art = validate_artefact_folder(vault_root, router, dest)
    if source_art["key"] != dest_art["key"]:
        raise ValueError(
            "Rename cannot move an artefact between different type folders. "
            "Use the convert operation for type changes."
        )
    validate_rename_request(vault_root, source, dest, router=router)
    validate_destination_parent_directory(vault_root, dest)
    links_updated = rename_and_update_links(
        vault_root,
        source,
        dest,
        router=router,
        prune_router=router,
    )
    return {
        "old_path": source,
        "new_path": dest,
        "links_updated": links_updated,
    }


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
            "Use artefact.unarchive first or remove the file manually."
        )
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    return abs_path


def delete_and_clean_links(
    vault_root,
    path,
    router=None,
    recursive=False,
    *,
    return_details=False,
    prune_router=None,
):
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
        prune_router: Optional compiled router that opts in to pruning the
                vacated owner folders once every file has been removed (see
                ``move_and_update_links``); never runs after a partial delete.

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

    orphaned_attachment_scopes = []
    attachment_keys = [source_key] if source_key else []
    attachment_keys.extend(entry["artefact_key"] for entry in descendants)
    for attachment_key in attachment_keys:
        scope = attachment_upload.existing_attachment_scope(
            vault_root, attachment_key
        )
        if scope:
            orphaned_attachment_scopes.append(scope)

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
    if prune_router is not None:
        prune_vacated_owner_folders(vault_root, removed, prune_router)
    if return_details:
        return {
            "links_replaced": links_replaced,
            "orphaned_attachment_scopes": orphaned_attachment_scopes,
            "deleted": removed,
        }
    return links_replaced


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="Rename a vault file and update matching wikilinks."
    )
    parser.add_argument("source")
    parser.add_argument("dest")
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    source, dest = args.source, args.dest
    vault_root = str(find_vault_root(args.vault))

    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        if args.json:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    try:
        with vault_mutation_lock(vault_root):
            links_updated = rename_and_update_links(
                vault_root, source, dest, router=router, prune_router=router,
            )
    except (MutationLockError, FileNotFoundError, ValueError, PartialApplyError, OSError) as e:
        message = public_mutation_error_message(e)
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)

    if args.json:
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
