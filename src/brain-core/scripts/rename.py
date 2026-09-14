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
from dataclasses import dataclass, field

from _common._wikilinks import (WikilinkRewritePlan, plan_wikilink_rewrites, apply_wikilink_rewrites)

import upload_attachment as attachment_upload

from _lifecycle.derived_cache_state import load_fresh_compiled_router
from _common import (
    build_md_basename_counts,
    build_wikilink_pattern,
    canonical_living_artefact_key,
    check_artefact_write_allowed,
    check_not_in_brain_core,
    check_write_allowed,
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
    abs_source, canonical_source = _resolve_move_path(vault_root, source)
    abs_dest, canonical_dest = _resolve_move_path(vault_root, dest)
    if os.path.islink(abs_source):
        raise ValueError(f"Move source cannot be a symlink: {source}")
    if os.path.lexists(abs_dest) and os.path.islink(abs_dest):
        raise ValueError(f"Move destination cannot be a symlink: {dest}")

    lexical_source = os.path.relpath(
        os.path.abspath(abs_source), os.path.abspath(vault_root)
    )
    if _move_source_scope(lexical_source) != _move_source_scope(canonical_source):
        raise ValueError(
            "Move source resolves into a different filesystem scope: "
            f"{source} -> {canonical_source}"
        )

    attachment_move = (
        allow_attachment_paths
        and _is_scoped_attachment_path(source)
        and _is_scoped_attachment_path(canonical_source)
        and _is_scoped_attachment_path(dest)
        and _is_scoped_attachment_path(canonical_dest)
    )
    if not attachment_move:
        if not (allow_archive_paths and _is_archived_move_path(canonical_dest)):
            check_artefact_write_allowed(canonical_dest)

    return abs_source, abs_dest


def _resolve_move_path(vault_root, path):
    """Return one lexical move path and its canonical vault-relative path."""
    absolute = os.path.join(vault_root, path)
    resolved = resolve_and_check_bounds(absolute, vault_root)
    canonical = os.path.relpath(resolved, os.path.realpath(vault_root))
    return absolute, canonical


def _is_archived_move_path(path):
    """Return whether a native or portable move path is archived."""
    return is_archived_path(str(path).replace("\\", "/"))


def _move_source_scope(path):
    """Classify a move source without granting authority to a different scope."""
    if _is_scoped_attachment_path(path):
        return "attachment"
    if _is_archived_move_path(path):
        return "archive"
    try:
        check_artefact_write_allowed(path)
    except ValueError:
        parts = str(path).replace("\\", "/").split("/")
        return f"protected:{parts[0] if parts else ''}"
    return "artefact"


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


@dataclass(frozen=True, slots=True)
class MoveLinksPlan:
    """Validated file moves and the precise link rewrites that precede them."""

    moves: tuple[dict, ...]
    ordered: tuple[dict, ...]
    links: WikilinkRewritePlan
    prune_router: dict | None = None
    link_counts: dict = field(default_factory=dict)
    allow_archive_paths: bool = False
    allow_attachment_paths: bool = False


class MoveApplyError(PartialApplyError):
    """A move batch failure retaining exact committed and failed move records."""

    def __init__(self, applied, failed, cause):
        self.applied = tuple(applied)
        self.failed = failed
        super().__init__(
            "move set partially applied — links already rewritten; "
            f"committed {applied}, failed at {failed['source']}->{failed['dest']}: {cause}")


def plan_move_and_links(vault_root, moves, *, router=None, allow_archive_paths=False,
                        allow_attachment_paths=False, prune_router=None,
                        overrides=None):
    """Resolve file identities and matching backlink writes without effects."""
    if router is None:
        planned = preflight_move_set(
            vault_root,
            moves,
            allow_archive_paths=allow_archive_paths,
            allow_attachment_paths=allow_attachment_paths,
        )
    else:
        planned = preflight_rename_move_set(
            vault_root,
            moves,
            router=router,
            allow_archive_paths=allow_archive_paths,
        )
    if not planned:
        return MoveLinksPlan((), (), WikilinkRewritePlan(()), prune_router)
    basename_counts = build_md_basename_counts(vault_root)
    pattern, stems = _combined_wikilink_plan(planned, basename_counts)
    owners = {}
    link_counts = {item["source"]: 0 for item in planned}
    for move in planned:
        _pattern, owned_stems = _combined_wikilink_plan([move], basename_counts)
        owners.update({stem: move["source"] for stem in owned_stems})
    replacer = make_wikilink_replacer(stems)

    def replace_and_count(match):
        link_counts[owners[match.group("stem")]] += 1
        return replacer(match)

    links = (plan_wikilink_rewrites(vault_root, pattern, replace_and_count,
                                   overrides=overrides)
             if pattern is not None else WikilinkRewritePlan(()))
    return MoveLinksPlan(tuple(planned), tuple(_ordered_moves_for_apply(planned)),
                         links, prune_router, link_counts,
                         allow_archive_paths, allow_attachment_paths)


def apply_move_and_links(vault_root, plan):
    """Apply admitted link writes and moves, preserving partial-apply reporting."""
    for move in plan.ordered:
        validate_move_path_request(
            vault_root, os.path.relpath(move["abs_source"], vault_root),
            os.path.relpath(move["abs_dest"], vault_root),
            allow_archive_paths=plan.allow_archive_paths,
            allow_attachment_paths=plan.allow_attachment_paths,
        )
    links_updated = apply_wikilink_rewrites(vault_root, plan.links)
    applied = []
    for move in plan.ordered:
        try:
            dest_dir = os.path.dirname(move["abs_dest"])
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            os.rename(move["abs_source"], move["abs_dest"])
        except OSError as exc:
            raise MoveApplyError(applied, move, exc) from exc
        applied.append({"source": move["source"], "dest": move["dest"]})

    if plan.prune_router is not None:
        prune_vacated_owner_folders(
            vault_root, [move["source"] for move in applied], plan.prune_router
        )

    return {
        "moves": [
            {"source": move["source"], "dest": move["dest"]}
            for move in plan.moves
        ],
        "applied": applied,
        "links_updated": links_updated,
    }



def move_and_update_links(vault_root, moves, *, allow_archive_paths=False,
                          allow_attachment_paths=False, prune_router=None):
    """Plan and apply a batch move; link writes precede moves and may partially commit."""
    return apply_move_and_links(vault_root, plan_move_and_links(
        vault_root, moves, allow_archive_paths=allow_archive_paths,
        allow_attachment_paths=allow_attachment_paths, prune_router=prune_router))


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


def plan_artefact_rename(vault_root, router, source, dest):
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
    return plan_move_and_links(vault_root, [{"source": source, "dest": dest}],
                               router=router, prune_router=router)


def apply_artefact_rename(vault_root, plan):
    """Apply one validated artefact rename."""
    result = apply_move_and_links(vault_root, plan)
    move = plan.moves[0]
    return {"old_path": move["source"], "new_path": move["dest"],
            "links_updated": result["links_updated"]}


def rename_artefact(vault_root, router, source, dest):
    """Rename within one configured type while preserving wikilinks."""
    return apply_artefact_rename(vault_root, plan_artefact_rename(vault_root, router, source, dest))


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


@dataclass(frozen=True, slots=True)
class DeletePlan:
    """The complete selected delete set and its matching backlink transforms."""

    paths: tuple[str, ...]
    abs_paths: tuple[str, ...]
    links: WikilinkRewritePlan
    orphaned_attachment_scopes: tuple[str, ...]
    prune_router: dict | None = None


def plan_artefact_delete(vault_root, path, router=None, recursive=False, *, prune_router=None):
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

    links = plan_wikilink_rewrites(vault_root, pattern, replacement)
    return DeletePlan(tuple(paths), tuple(abs_paths), links,
                      tuple(orphaned_attachment_scopes), prune_router)


def apply_artefact_delete(vault_root, plan):
    """Apply admitted backlink writes and deletes with partial-effect reporting."""
    links_replaced = apply_wikilink_rewrites(vault_root, plan.links)
    removed = []
    for rel_path, abs_delete_path in zip(plan.paths, plan.abs_paths):
        try:
            os.remove(abs_delete_path)
        except OSError as exc:
            raise PartialApplyError(
                f"delete set partially applied — links already rewritten; "
                f"removed {removed}, failed at {rel_path}: {exc}"
            ) from exc
        removed.append(rel_path)
    if plan.prune_router is not None:
        prune_vacated_owner_folders(vault_root, removed, plan.prune_router)
    return {"links_replaced": links_replaced,
            "orphaned_attachment_scopes": list(plan.orphaned_attachment_scopes),
            "deleted": removed}


def delete_and_clean_links(vault_root, path, router=None, recursive=False, *,
                           return_details=False, prune_router=None):
    """Delete a selected artefact subtree and strike through its backlinks."""
    result = apply_artefact_delete(vault_root, plan_artefact_delete(
        vault_root, path, router, recursive, prune_router=prune_router))
    return result if return_details else result["links_replaced"]


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
