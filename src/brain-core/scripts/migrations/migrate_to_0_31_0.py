#!/usr/bin/env python3
"""
migrate_to_0_31_0.py — Brain Hub Key Convention migration.

Three phases:

1. **Key backfill** — stamp every living artefact with a valid canonical
   ``key:`` field. Priority: existing valid key → promote legacy
   ``hub-slug`` / ``hub_slug`` → self-referencing type tag → title-derived
   key with numeric collision suffix → generated fallback. When ownership
   is proved by folder residency, ``parent: {type}/{key}`` and the
   corresponding self/owner tags are backfilled in the same pass.
2. **Child folder relocations** — move living artefacts so their folder
   matches the canonical form implied by their ``parent:`` (or a single
   resolvable hub-tag fallback). Wikilinks are rewritten atomically per
   move via ``rename_and_update_links``.
3. **Workspace reconciliation** — rename ``_Workspaces/{old}/`` data folders
   to their canonical key and rewrite ``.brain/local/workspaces.json`` keys.

The migration is idempotent. Rollback is handled by the upgrade runner's
snapshot context — this script does not manage its own backup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import (
    STATUS_FOLDER_PREFIX,
    artefact_type_prefix,
    ensure_parent_tag,
    ensure_self_tag,
    find_vault_root,
    generate_contextual_slug,
    is_archived_path,
    is_valid_key,
    iter_living_markdown_files,
    load_compiled_router,
    make_artefact_key,
    normalize_artefact_key,
    read_artefact,
    read_frontmatter,
    resolve_and_validate_folder,
    resolve_artefact_key_entry,
    resolve_folder,
    safe_write,
    serialize_frontmatter,
    title_to_slug,
)
from compile_router import build_living_artefact_index
from rename import rename_and_update_links
from workspace_registry import EMBEDDED_DATA_DIR, load_registry, save_registry


VERSION = "0.31.0"
LEGACY_SLUG_KEYS = ("hub-slug", "hub_slug")


# ---------------------------------------------------------------------------
# Field-placement helpers
# ---------------------------------------------------------------------------

def _insert_field_after(fields, key, value, anchors, drop=()):
    """Return a new dict with ``key=value`` inserted after the first anchor present."""
    drop_set = set(drop) | {key}
    anchor = next((a for a in anchors if a in fields), None)

    ordered = {}
    inserted = False
    if anchor is None:
        ordered[key] = value
        inserted = True

    for k, v in fields.items():
        if k in drop_set:
            continue
        ordered[k] = v
        if not inserted and k == anchor:
            ordered[key] = value
            inserted = True

    if not inserted:
        ordered[key] = value
    return ordered


def _insert_key_field(fields, key):
    """Place ``key`` after ``tags`` (or ``type``), dropping any legacy slug keys."""
    return _insert_field_after(fields, "key", key, ("tags", "type"), drop=LEGACY_SLUG_KEYS)


def _set_parent_field(fields, parent_key):
    """Place ``parent`` after ``key`` (or ``tags``/``type``)."""
    return _insert_field_after(fields, "parent", parent_key, ("key", "tags", "type"))


# ---------------------------------------------------------------------------
# Phase 1 — Slug backfill (with folder-residency parent + self-tag backfill)
# ---------------------------------------------------------------------------

def _derive_key(fields, stem, type_prefix, taken):
    """Apply priority order to derive a key value.

    Returns (slug, source). ``source`` ∈ {existing, hub-slug, hub_slug,
    self_tag, title, generated}. Caller adds the returned slug to ``taken``.
    """
    existing = fields.get("key")
    if is_valid_key(existing):
        return existing, "existing"

    for legacy_key in LEGACY_SLUG_KEYS:
        legacy = fields.get(legacy_key)
        if is_valid_key(legacy):
            return legacy, legacy_key

    tags = fields.get("tags") or []
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, str):
                continue
            canonical = normalize_artefact_key(tag)
            if not canonical:
                continue
            prefix, candidate = canonical.split("/", 1)
            if prefix == type_prefix and is_valid_key(candidate):
                return candidate, "self_tag"

    title = fields.get("title")
    source_text = title.strip() if isinstance(title, str) and title.strip() else stem
    base = title_to_slug(source_text)
    if is_valid_key(base):
        candidate = base
        counter = 2
        while candidate in taken:
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate, "title"

    candidate = generate_contextual_slug(source_text)
    while candidate in taken:
        candidate = generate_contextual_slug(source_text)
    return candidate, "generated"


def _resolve_folder_parent(art, rel_path, type_prefix, own_key, entry_by_key):
    """Return the canonical parent key implied by a file's folder residency.

    ``None`` when the file has no single parent-token folder, the token does
    not resolve to an existing living key, or the implied parent is the file
    itself.
    """
    parent_token = _parent_token_for_path(art, rel_path)
    if not parent_token:
        return None
    inferred = normalize_artefact_key(parent_token)
    if not inferred and is_valid_key(parent_token):
        inferred = make_artefact_key(type_prefix, parent_token)
    if inferred is None or inferred == own_key or inferred not in entry_by_key:
        return None
    return inferred


def _parent_token_for_path(art, rel_path):
    """Return the single owner-folder token from a living artefact path, if any."""
    current_folder = os.path.dirname(rel_path)
    rel_to_type = os.path.relpath(current_folder, art["path"])
    if rel_to_type in {".", ""}:
        return None
    parts = [p for p in rel_to_type.split(os.sep) if p]
    if parts and parts[-1].startswith(STATUS_FOLDER_PREFIX):
        parts = parts[:-1]
    if len(parts) != 1:
        return None
    return parts[0]


def plan_key_backfill(vault_root, router):
    """Plan Phase 1. Returns list of plans; never writes."""
    files = list(iter_living_markdown_files(vault_root, router, include_status_folders=True))

    records = []
    taken_by_type: dict[str, set[str]] = {}

    # Pass 1: preseed taken keys with any already-valid values so derivation
    # in pass 2 doesn't collide with them.
    for rel_path in files:
        abs_path = os.path.join(vault_root, rel_path)
        try:
            fields = read_frontmatter(abs_path)
        except (OSError, UnicodeDecodeError):
            continue
        try:
            _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        except ValueError:
            continue
        slug = fields.get("key")
        if is_valid_key(slug):
            taken_by_type.setdefault(artefact_type_prefix(art), set()).add(slug)

    # Pass 2: derive keys (and record every living artefact for the
    # subsequent parent-backfill pass).
    for rel_path in files:
        abs_path = os.path.join(vault_root, rel_path)
        try:
            fields, body = read_artefact(abs_path)
        except (OSError, UnicodeDecodeError):
            continue
        try:
            _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        except ValueError:
            continue
        type_prefix = artefact_type_prefix(art)
        stem = os.path.splitext(os.path.basename(rel_path))[0]
        taken = taken_by_type.setdefault(type_prefix, set())
        new_key, source = _derive_key(fields, stem, type_prefix, taken)
        taken.add(new_key)

        records.append({
            "rel_path": rel_path,
            "fields": fields,
            "body": body,
            "art": art,
            "type_prefix": type_prefix,
            "new_key": new_key,
            "source": source,
            "legacy_keys": [k for k in LEGACY_SLUG_KEYS if k in fields],
        })

    entry_by_key = {
        make_artefact_key(r["type_prefix"], r["new_key"]): r["rel_path"]
        for r in records
    }

    plans = []
    for r in records:
        rel_path = r["rel_path"]
        fields = dict(r["fields"])
        new_key = r["new_key"]
        type_prefix = r["type_prefix"]
        art = r["art"]

        key_needs_write = fields.get("key") != new_key or bool(r["legacy_keys"])

        working_fields = _insert_key_field(fields, new_key) if key_needs_write else dict(fields)
        tag_changed = ensure_self_tag(working_fields, type_prefix, new_key)

        own_key = make_artefact_key(type_prefix, new_key)
        inferred_parent = _resolve_folder_parent(
            art, rel_path, type_prefix, own_key, entry_by_key,
        )
        current_parent = normalize_artefact_key(working_fields.get("parent"))
        parent_changed = False
        if inferred_parent and current_parent != inferred_parent:
            working_fields = _set_parent_field(working_fields, inferred_parent)
            ensure_parent_tag(working_fields)
            parent_changed = True

        if not (key_needs_write or tag_changed or parent_changed):
            continue

        plans.append({
            "rel_path": rel_path,
            "fields": working_fields,
            "body": r["body"],
            "new_key": new_key,
            "source": r["source"],
            "type_prefix": type_prefix,
            "legacy_keys": r["legacy_keys"],
            "parent_backfilled": inferred_parent if parent_changed else None,
        })

    return plans


def apply_key_backfill(vault_root, plans):
    """Write Phase 1 plans to disk."""
    written = []
    for p in plans:
        content = serialize_frontmatter(p["fields"], p["body"])
        abs_path = os.path.join(vault_root, p["rel_path"])
        safe_write(abs_path, content, bounds=vault_root)
        written.append(p["rel_path"])
    return written


# ---------------------------------------------------------------------------
# Phase 2 — Child folder relocations
# ---------------------------------------------------------------------------

def _single_tag_parent(fields, router):
    """Return canonical parent key derivable from a single resolvable hub-tag.

    Mirrors ``check_parent_contract``'s fallback: if exactly one tag resolves
    to a living-artefact key, treat it as the implicit parent.
    """
    tags = fields.get("tags") or []
    if not isinstance(tags, list):
        return None
    candidates = []
    for tag in tags:
        normalized = normalize_artefact_key(tag)
        if not normalized:
            continue
        if not resolve_artefact_key_entry(router, normalized):
            continue
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates[0] if len(candidates) == 1 else None


def plan_folder_relocations(vault_root, router):
    """Plan Phase 2. Returns (moves, orphans)."""
    moves = []
    orphans = []

    for rel_path in iter_living_markdown_files(vault_root, router, include_status_folders=True):
        if is_archived_path(rel_path):
            continue
        abs_path = os.path.join(vault_root, rel_path)
        try:
            fields = read_frontmatter(abs_path)
        except (OSError, UnicodeDecodeError):
            continue
        try:
            _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        except ValueError:
            continue

        current_folder = os.path.dirname(rel_path)
        base_folder = (
            os.path.dirname(current_folder)
            if os.path.basename(current_folder).startswith(STATUS_FOLDER_PREFIX)
            else current_folder
        )
        trailing = os.path.relpath(current_folder, base_folder) if base_folder else ""

        own_key = None
        own_slug = fields.get("key")
        if is_valid_key(own_slug):
            own_key = make_artefact_key(artefact_type_prefix(art), own_slug)

        parent_key = normalize_artefact_key(fields.get("parent"))
        if parent_key == own_key:
            parent_key = None
        if parent_key and not resolve_artefact_key_entry(router, parent_key):
            parent_key = None
        if parent_key is None:
            candidate = _single_tag_parent(fields, router)
            if candidate != own_key:
                parent_key = candidate

        if parent_key:
            try:
                expected = resolve_folder(art, parent=parent_key, fields=fields, router=router)
            except ValueError:
                continue
            stored_parent = normalize_artefact_key(fields.get("parent"))
            needs_parent_backfill = stored_parent != parent_key
            if base_folder != expected or needs_parent_backfill:
                dest_folder = expected
                if trailing and trailing != ".":
                    dest_folder = os.path.join(dest_folder, trailing)
                moves.append({
                    "source": rel_path,
                    "dest": os.path.join(dest_folder, os.path.basename(rel_path)),
                    "reason": f"parent={parent_key}",
                    "backfill_parent": parent_key if needs_parent_backfill else None,
                })
            continue

        # No parent resolvable. If the file sits inside a subfolder of its
        # type's base path, that subfolder looks like a parent reference —
        # flag it as an orphan so the user notices.
        if base_folder != art["path"]:
            orphans.append({
                "path": rel_path,
                "subfolder": base_folder,
                "reason": "No resolvable parent (no canonical `parent:` field and no single-tag fallback).",
            })

    return moves, orphans


def _backfill_parent_in_file(vault_root, rel_path, parent_key):
    """Read the file, set ``parent: parent_key`` in frontmatter, write it back."""
    abs_path = os.path.join(vault_root, rel_path)
    fields, body = read_artefact(abs_path)
    new_fields = _set_parent_field(fields, parent_key)
    safe_write(abs_path, serialize_frontmatter(new_fields, body), bounds=vault_root)


def apply_folder_relocations(vault_root, moves, router):
    """Execute planned moves. Returns list of per-move results."""
    results = []
    for move in moves:
        try:
            if move.get("backfill_parent"):
                _backfill_parent_in_file(vault_root, move["source"], move["backfill_parent"])
            if move["source"] == move["dest"]:
                results.append({
                    "source": move["source"],
                    "dest": move["dest"],
                    "links_updated": 0,
                    "reason": move["reason"],
                })
                continue
            links = rename_and_update_links(
                vault_root, move["source"], move["dest"], router=router,
            )
            results.append({
                "source": move["source"],
                "dest": move["dest"],
                "links_updated": links,
                "reason": move["reason"],
            })
        except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
            results.append({
                "source": move["source"],
                "dest": move["dest"],
                "error": str(exc),
                "reason": move["reason"],
            })
    return results


# ---------------------------------------------------------------------------
# Phase 3 — Workspace reconciliation
# ---------------------------------------------------------------------------

def plan_workspace_reconciliation(vault_root, router):
    """Plan Phase 3. Returns {'folder_renames': [...], 'registry_remaps': [...]}."""
    folder_renames = []
    registry_remaps = []

    workspace_artefacts = [
        a for a in router.get("artefacts", [])
        if artefact_type_prefix(a) == "workspace"
    ]
    if not workspace_artefacts:
        return {"folder_renames": folder_renames, "registry_remaps": registry_remaps}

    data_root = os.path.join(vault_root, EMBEDDED_DATA_DIR)
    existing_dirs = set()
    if os.path.isdir(data_root):
        existing_dirs = {
            entry for entry in os.listdir(data_root)
            if os.path.isdir(os.path.join(data_root, entry))
            and not entry.startswith((".", "_"))
        }

    key_to_stem = {}

    for rel_path in iter_living_markdown_files(vault_root, router, include_status_folders=False):
        try:
            _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        except ValueError:
            continue
        if artefact_type_prefix(art) != "workspace":
            continue
        abs_path = os.path.join(vault_root, rel_path)
        try:
            fields = read_frontmatter(abs_path)
        except (OSError, UnicodeDecodeError):
            continue
        slug = fields.get("key")
        if not is_valid_key(slug):
            continue
        stem = os.path.splitext(os.path.basename(rel_path))[0]
        key_to_stem[slug] = stem

        if slug in existing_dirs:
            continue
        if stem in existing_dirs:
            folder_renames.append({
                "from": os.path.join(EMBEDDED_DATA_DIR, stem),
                "to": os.path.join(EMBEDDED_DATA_DIR, slug),
                "key": slug,
            })

    stem_to_key = {stem: slug for slug, stem in key_to_stem.items() if stem != slug}
    registry = load_registry(vault_root)
    for key in list(registry.keys()):
        if key in key_to_stem:
            continue
        target_slug = stem_to_key.get(key)
        if target_slug:
            registry_remaps.append({
                "from": key,
                "to": target_slug,
                "path": registry[key].get("path"),
            })

    return {"folder_renames": folder_renames, "registry_remaps": registry_remaps}


def apply_workspace_reconciliation(vault_root, plan):
    """Execute Phase 3 plans."""
    results = {"folder_renames": [], "registry_remaps": []}

    for rename in plan["folder_renames"]:
        src = os.path.join(vault_root, rename["from"])
        dst = os.path.join(vault_root, rename["to"])
        try:
            os.rename(src, dst)
            results["folder_renames"].append(rename)
        except OSError as exc:
            results["folder_renames"].append({**rename, "error": str(exc)})

    if plan["registry_remaps"]:
        registry = load_registry(vault_root)
        for remap in plan["registry_remaps"]:
            if remap["from"] in registry:
                registry[remap["to"]] = registry.pop(remap["from"])
                results["registry_remaps"].append(remap)
        save_registry(vault_root, registry)

    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def migrate_vault(vault_root, *, apply=False, router=None):
    """Run the full three-phase migration. Returns a structured result dict."""
    vault_root = os.path.abspath(vault_root)

    if router is None:
        router = load_compiled_router(vault_root)
    if "error" in router:
        return {
            "dry_run": not apply,
            "error": router["error"],
            "phase1": {"planned": 0, "applied": 0, "plans": []},
            "phase2": {"planned": 0, "applied": 0, "moves": [], "orphans": []},
            "phase3": {"folder_renames": [], "registry_remaps": []},
        }

    # Phase 1
    phase1_plans = plan_key_backfill(vault_root, router)
    phase1_applied = []
    if apply and phase1_plans:
        phase1_applied = apply_key_backfill(vault_root, phase1_plans)
        # Rebuild the in-memory artefact_index so Phase 2 sees new keys.
        new_index = build_living_artefact_index(vault_root, router.get("artefacts", []))
        router = dict(router)
        router["artefact_index"] = new_index

    # Phase 2
    phase2_moves, phase2_orphans = plan_folder_relocations(vault_root, router)
    phase2_applied = []
    if apply and phase2_moves:
        phase2_applied = apply_folder_relocations(vault_root, phase2_moves, router)

    # Phase 3
    phase3_plan = plan_workspace_reconciliation(vault_root, router)
    phase3_applied = {"folder_renames": [], "registry_remaps": []}
    if apply and (phase3_plan["folder_renames"] or phase3_plan["registry_remaps"]):
        phase3_applied = apply_workspace_reconciliation(vault_root, phase3_plan)

    return {
        "dry_run": not apply,
        "phase1": {
            "planned": len(phase1_plans),
            "applied": len(phase1_applied),
            "plans": [
                {
                    "path": p["rel_path"],
                    "key": p["new_key"],
                    "source": p["source"],
                    "legacy_keys": p["legacy_keys"],
                    "parent_backfilled": p.get("parent_backfilled"),
                }
                for p in phase1_plans
            ],
        },
        "phase2": {
            "planned": len(phase2_moves),
            "applied": len(phase2_applied),
            "moves": phase2_applied if apply else phase2_moves,
            "orphans": phase2_orphans,
        },
        "phase3": phase3_applied if apply else phase3_plan,
    }


def migrate(vault_root: str) -> dict:
    """Entry point used by upgrade.py after compile succeeds."""
    result = migrate_vault(vault_root, apply=True)
    if result.get("error"):
        return {"status": "error", "version": VERSION, "error": result["error"]}
    return {"status": "ok", "version": VERSION, **result}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_human(result):
    prefix = "[DRY RUN] " if result.get("dry_run") else ""

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    p1 = result["phase1"]
    print(f"Phase 1 — key backfill: {p1['planned']} planned, {p1['applied']} applied")
    source_counts = {}
    for plan in p1["plans"]:
        source_counts[plan["source"]] = source_counts.get(plan["source"], 0) + 1
    for source in sorted(source_counts):
        print(f"  {source}: {source_counts[source]}")
    for plan in p1["plans"]:
        tail = f" (parent={plan['parent_backfilled']})" if plan.get("parent_backfilled") else ""
        print(f"  {prefix}{plan['path']} → key={plan['key']} (via {plan['source']}){tail}")

    p2 = result["phase2"]
    print(f"\nPhase 2 — folder relocations: {p2['planned']} planned")
    for move in p2["moves"]:
        if "error" in move:
            print(f"  ERROR {move['source']} → {move['dest']}: {move['error']}", file=sys.stderr)
        else:
            links = move.get("links_updated", 0)
            tail = f" ({links} links)" if links else ""
            print(f"  {prefix}{move['source']} → {move['dest']}{tail}  [{move['reason']}]")
    if p2["orphans"]:
        print("\nManual attention needed (orphans):")
        for orphan in p2["orphans"]:
            print(f"  {orphan['path']} — {orphan['reason']}")

    p3 = result["phase3"]
    renames = p3.get("folder_renames", [])
    remaps = p3.get("registry_remaps", [])
    print(f"\nPhase 3 — workspace reconciliation: {len(renames)} folder rename(s), "
          f"{len(remaps)} registry remap(s)")
    for r in renames:
        print(f"  {prefix}{r['from']} → {r['to']}")
    for r in remaps:
        print(f"  {prefix}registry key: {r['from']} → {r['to']}")

    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    vault_root = str(find_vault_root(args.vault))
    result = migrate_vault(vault_root, apply=not args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if not result.get("error") else 1

    return _print_human(result)


if __name__ == "__main__":
    raise SystemExit(main())
