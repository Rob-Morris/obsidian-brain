#!/usr/bin/env python3
"""
workspace_registry.py — Workspace slug-to-path resolution.

Maps workspace slugs to their data folder paths. Embedded workspaces
resolve implicitly (slug → _Workspaces/slug/). Linked workspaces store
their external path in .brain/local/workspaces.json.

The MCP server loads the registry on startup and uses it to resolve
workspace file operations. The registry is machine-local config (in
.brain/local/, gitignored) — the vault remains portable.

Usage:
    python3 workspace_registry.py                # list all workspaces
    python3 workspace_registry.py --vault /path
    python3 workspace_registry.py --register slug /path/to/data
    python3 workspace_registry.py --unregister slug
"""

import json
import os
import sys

from _common import (
    find_vault_root,
    is_system_dir,
    is_valid_key,
    iter_markdown_under,
    read_frontmatter,
    safe_write_json,
    slug_to_title,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_LOCAL_DIR = os.path.join(".brain", "local")
REGISTRY_FILE = "workspaces.json"
REGISTRY_REL = os.path.join(BRAIN_LOCAL_DIR, REGISTRY_FILE)
EMBEDDED_DATA_DIR = "_Workspaces"
HUB_DIR = "Workspaces"


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def _registry_path(vault_root):
    """Return absolute path to .brain/local/workspaces.json."""
    return os.path.join(vault_root, REGISTRY_REL)


def load_registry(vault_root):
    """Load the linked workspace registry from .brain/local/workspaces.json.

    Returns a dict of slug → {"path": absolute_path}.
    Returns empty dict if the file doesn't exist.
    Normalises bare-string entries to {"path": value}.
    """
    path = _registry_path(vault_root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    raw = data.get("workspaces", {})
    return {
        slug: (entry if isinstance(entry, dict) else {"path": entry})
        for slug, entry in raw.items()
    }


def save_registry(vault_root, registry):
    """Write the linked workspace registry to .brain/local/workspaces.json.

    Args:
        vault_root: Absolute path to vault root.
        registry: Dict of slug → {"path": absolute_path}.
    """
    path = _registry_path(vault_root)
    safe_write_json(path, {"workspaces": registry}, bounds=str(vault_root))


# ---------------------------------------------------------------------------
# Discovery — embedded workspaces
# ---------------------------------------------------------------------------

def _scan_embedded(vault_root):
    """Discover embedded workspaces from _Workspaces/ subdirectories.

    Returns a dict of slug → {"path": absolute_path, "mode": "embedded"}.
    """
    data_dir = os.path.join(vault_root, EMBEDDED_DATA_DIR)
    if not os.path.isdir(data_dir):
        return {}
    result = {}
    for entry in sorted(os.listdir(data_dir)):
        full = os.path.join(data_dir, entry)
        if not os.path.isdir(full):
            continue
        if is_system_dir(entry):
            continue
        result[entry] = {"path": full, "mode": "embedded"}
    return result


# Per-hub frontmatter cache keyed by absolute path → (mtime, slug, metadata).
# Skips the read on unchanged hubs; matters as completed workspaces accumulate
# in +Completed/ over the lifetime of the brain (terminal status, never deleted).
_hub_metadata_cache: dict[str, tuple[float, str, dict]] = {}


def _scan_hub_metadata(vault_root):
    """Read workspace hub artefacts from Workspaces/ for metadata enrichment.

    Walks the hub dir including ``+*`` terminal-status folders, so completed
    workspace hubs (which move to ``Workspaces/+Completed/`` per the
    artefact lifecycle) are picked up alongside active ones.

    Keys the result dict by the canonical frontmatter ``key:`` when present,
    falling back to the filename stem for pre-0.31 hubs that have not yet
    been migrated. Returns a dict of key → {title, status, workspace_mode, tags}.
    """
    hub_dir = os.path.join(vault_root, HUB_DIR)
    if not os.path.isdir(hub_dir):
        return {}
    seen = set()
    result = {}
    for sub_rel in iter_markdown_under(hub_dir, include_status_folders=True):
        fpath = os.path.join(hub_dir, sub_rel)
        seen.add(fpath)
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            continue
        cached = _hub_metadata_cache.get(fpath)
        if cached is not None and cached[0] == mtime:
            key, entry = cached[1], cached[2]
        else:
            try:
                fields = read_frontmatter(fpath)
            except OSError:
                continue
            stem = os.path.splitext(os.path.basename(sub_rel))[0]
            fm_key = fields.get("key")
            key = fm_key if is_valid_key(fm_key) else stem
            entry = {
                "title": fields.get("title") or stem,
                "status": fields.get("status", ""),
                "workspace_mode": fields.get("workspace_mode", ""),
                "tags": fields.get("tags", []),
                "hub_path": os.path.join(HUB_DIR, sub_rel),
            }
            _hub_metadata_cache[fpath] = (mtime, key, entry)
        # Shallow-copy on emit so callers can't mutate the cached entry
        # (tags is the only list field, but copy it explicitly).
        result[key] = {**entry, "tags": list(entry["tags"])}
    for stale in set(_hub_metadata_cache) - seen:
        del _hub_metadata_cache[stale]
    return result


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_workspace(vault_root, slug, registry=None):
    """Resolve a workspace slug to its absolute data folder path.

    Resolution order:
      1. Embedded: _Workspaces/{slug}/ exists → return that path
      2. Linked: slug is in the registry → return registered path

    Args:
        vault_root: Absolute path to vault root.
        slug: Workspace slug to resolve.
        registry: Pre-loaded registry dict (loaded from disk if None).

    Returns:
        dict with "path", "mode", and "slug".

    Raises:
        ValueError: If the slug cannot be resolved.
    """
    # Check embedded first
    embedded_path = os.path.join(vault_root, EMBEDDED_DATA_DIR, slug)
    if os.path.isdir(embedded_path):
        return {"slug": slug, "path": embedded_path, "mode": "embedded"}

    if registry is None:
        registry = load_registry(vault_root)
    if slug in registry:
        path = os.path.expanduser(registry[slug]["path"])
        return {"slug": slug, "path": path, "mode": "linked"}

    raise ValueError(
        f"Unknown workspace '{slug}'. "
        f"No embedded data folder at _Workspaces/{slug}/ "
        f"and no linked registration in .brain/local/workspaces.json."
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def _make_entry(slug, mode, path, hub_meta):
    """Build a workspace list entry, enriched with hub metadata."""
    meta = hub_meta.get(slug, {})
    return {
        "slug": slug,
        "mode": mode,
        "path": path,
        "hub_path": meta.get("hub_path", ""),
        "title": meta.get("title", slug_to_title(slug)),
        "status": meta.get("status", ""),
        "tags": meta.get("tags", []),
    }


def list_workspaces(vault_root, registry=None):
    """List all workspaces (embedded + linked), enriched with hub metadata.

    Returns a list of dicts, each with: slug, mode, path, hub_path,
    title, status, tags. Hub metadata is best-effort — missing hub
    artefacts result in empty fields.
    """
    if registry is None:
        registry = load_registry(vault_root)

    embedded = _scan_embedded(vault_root)

    if not embedded and not registry:
        return []

    hub_meta = _scan_hub_metadata(vault_root)
    workspaces = []

    for slug, info in embedded.items():
        workspaces.append(_make_entry(slug, "embedded", info["path"], hub_meta))

    for slug, entry in registry.items():
        path = os.path.expanduser(entry["path"])
        workspaces.append(_make_entry(slug, "linked", path, hub_meta))

    return workspaces


# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------

def register_workspace(vault_root, slug, path):
    """Register a linked workspace in .brain/local/workspaces.json.

    Args:
        vault_root: Absolute path to vault root.
        slug: Workspace slug (e.g. "my-project").
        path: Absolute path to the external data folder.

    Returns:
        dict with status and registration details.

    Raises:
        ValueError: If slug conflicts with an embedded workspace.
    """
    # Check for embedded conflict
    embedded_path = os.path.join(vault_root, EMBEDDED_DATA_DIR, slug)
    if os.path.isdir(embedded_path):
        raise ValueError(
            f"Cannot register linked workspace '{slug}' — "
            f"an embedded workspace already exists at _Workspaces/{slug}/."
        )

    path = os.path.abspath(os.path.expanduser(path))

    registry = load_registry(vault_root)
    was_update = slug in registry
    registry[slug] = {"path": path}
    save_registry(vault_root, registry)

    return {
        "status": "ok",
        "action": "updated" if was_update else "registered",
        "slug": slug,
        "path": path,
        "mode": "linked",
    }


def unregister_workspace(vault_root, slug):
    """Remove a linked workspace from .brain/local/workspaces.json.

    Args:
        vault_root: Absolute path to vault root.
        slug: Workspace slug to remove.

    Returns:
        dict with status.

    Raises:
        ValueError: If slug is not in the registry.
    """
    registry = load_registry(vault_root)
    if slug not in registry:
        raise ValueError(
            f"Workspace '{slug}' is not registered as a linked workspace. "
            f"Only linked workspaces (in .brain/local/workspaces.json) can be unregistered."
        )

    del registry[slug]
    save_registry(vault_root, registry)

    return {"status": "ok", "action": "unregistered", "slug": slug}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Workspace registry management")
    parser.add_argument("--vault", help="Vault root (auto-detected if omitted)")
    parser.add_argument("--register", nargs=2, metavar=("SLUG", "PATH"),
                        help="Register a linked workspace")
    parser.add_argument("--unregister", metavar="SLUG",
                        help="Unregister a linked workspace")
    parser.add_argument("--resolve", metavar="SLUG",
                        help="Resolve a workspace slug to its path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    vault_root = str(find_vault_root(args.vault))

    if args.register:
        slug, path = args.register
        result = register_workspace(vault_root, slug, path)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['action']}: {slug} → {path}")

    elif args.unregister:
        result = unregister_workspace(vault_root, args.unregister)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Unregistered: {args.unregister}")

    elif args.resolve:
        try:
            result = resolve_workspace(vault_root, args.resolve)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"{result['slug']} ({result['mode']}): {result['path']}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        workspaces = list_workspaces(vault_root)
        if args.json:
            print(json.dumps(workspaces, indent=2))
        else:
            if not workspaces:
                print("No workspaces registered.")
            else:
                for ws in workspaces:
                    print(f"  {ws['slug']} ({ws['mode']}): {ws['path']}")


if __name__ == "__main__":
    main()
