#!/usr/bin/env python3
"""
workspace_registry.py — Workspace slug-to-path resolution.

Maps workspace slugs to their data folder paths. Embedded workspaces
resolve implicitly (slug → _Workspaces/slug/). Linked workspaces store
their external path in .brain/local/workspaces.json.

Workspace listing, reading and path resolution read it. The registry is
machine-local config (in .brain/local/, gitignored), so the vault remains
portable.

Each row is the Brain end of a link whose workspace end is the manifest in
the recorded folder. Its writers are ``workspace.setup``, which writes a row
when it makes a link; ``workspace.unregister``, which drops one;
``workspace.repair-registry``, which rebuilds a malformed file without its
invalid rows and drops a row whose manifest names another Brain or
workspace, with no lock in, and no write to, any workspace folder; and MCP
configuration, which stages the row its manifest implies.

Usage:
    python3 workspace_registry.py                # list all workspaces
    python3 workspace_registry.py --vault /path
    python3 workspace_registry.py --resolve slug
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
import sys
from typing import TYPE_CHECKING

from _common._artefacts import iter_markdown_under
from _common._filesystem import safe_write_json
from _common._frontmatter import read_frontmatter
from _common._slugs import is_valid_key, slug_to_title
from _common._vault import find_vault_root, is_system_dir

if TYPE_CHECKING:
    from _bootstrap.workspace_binding import LinkClassification

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_LOCAL_DIR = os.path.join(".brain", "local")
REGISTRY_FILE = "workspaces.json"
REGISTRY_REL = os.path.join(BRAIN_LOCAL_DIR, REGISTRY_FILE)
EMBEDDED_DATA_DIR = "_Workspaces"
HUB_DIR = "Workspaces"


class UnknownWorkspaceError(ValueError):
    """Raised when an exact workspace slug is absent from every registry plane."""


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def canonical_path(path):
    """The one form in which a linked workspace folder is stored and compared."""
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def salvage_row(key, entry):
    """The canonical row ``{"path": canonical_path(...)}`` a stored entry names, or ``None``.

    The registry's one row rule, used by every reader and writer: the key is a
    valid key and the path a non-empty absolute string with no NUL byte
    (``~`` counts as absolute and is expanded). Anything else names no usable
    folder: it is never resolved against the current directory and never
    classified, and a rebuild drops it. A valid row stored in another form (a
    bare string, ``~``, a trailing slash, extra fields) differs from its
    canonical form, so the file needs normalising.
    """
    path = entry if isinstance(entry, str) else entry.get("path") if isinstance(entry, dict) else None
    if not is_valid_key(key) or not isinstance(path, str) or not path or "\0" in path:
        return None
    if not os.path.isabs(os.path.expanduser(path)):
        return None
    return {"path": canonical_path(path)}


def row_records(entry, folder):
    """Whether a registry row records ``folder``: its path only, both sides in canonical form."""
    path = entry.get("path") if isinstance(entry, dict) else None
    return isinstance(path, str) and canonical_path(path) == canonical_path(folder)


def is_embedded(vault_root, key):
    """Whether ``key`` names an embedded workspace, whose data folder is inside the vault."""
    return os.path.isdir(os.path.join(vault_root, EMBEDDED_DATA_DIR, key))


class RegistryChangedError(ValueError):
    """The registry changed between the read a write was planned from and the write; nothing was written."""


def _registry_path(vault_root):
    """Return absolute path to .brain/local/workspaces.json."""
    return os.path.join(vault_root, REGISTRY_REL)


def read_registry_bytes(vault_root):
    """The registry file's bytes, or ``None`` when there is no file; the base for a compare-and-swap write."""
    try:
        with open(_registry_path(vault_root), "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def decode_registry(content):
    """The ``workspaces`` object a registry file holds; ``ValueError`` when its rows cannot be read."""
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid linked workspace registry: {exc}") from exc
    workspaces = data.get("workspaces", {}) if isinstance(data, dict) else None
    if not isinstance(workspaces, dict):
        raise ValueError("Invalid linked workspace registry: workspaces must be an object")
    return workspaces


def load_registry(vault_root):
    """The salvageable rows of .brain/local/workspaces.json, ``{}`` when it is absent or unreadable.

    Lenient: a row that names no usable folder is left out (``salvage_row``),
    and every path is canonical, so no caller resolves a row against the
    current directory.
    """
    try:
        content = read_registry_bytes(vault_root)
        workspaces = {} if content is None else decode_registry(content)
    except (OSError, ValueError):
        return {}
    rows = {key: salvage_row(key, entry) for key, entry in workspaces.items()}
    return {key: row for key, row in rows.items() if row is not None}


def load_raw_registry(vault_root):
    """Every stored entry as written, unvalidated: only the historical 0.31.0 key migration reads this.

    Its keys may predate the key contract, which is what that migration
    remaps; no caller may resolve these paths. ``{}`` when absent or unreadable.
    """
    try:
        content = read_registry_bytes(vault_root)
        workspaces = {} if content is None else decode_registry(content)
    except (OSError, ValueError):
        return {}
    return {key: entry if isinstance(entry, dict) else {"path": entry} for key, entry in workspaces.items()}


def read_registry_strict(vault_root):
    """The canonical rows and the exact bytes they were parsed from, failing on any invalid row."""
    try:
        content = read_registry_bytes(vault_root)
    except OSError as exc:
        raise ValueError(f"Invalid linked workspace registry: {exc}") from exc
    if content is None:
        return {}, None
    registry = {}
    for key, entry in decode_registry(content).items():
        row = salvage_row(key, entry)
        if row is None:
            raise ValueError(f"Invalid linked workspace registry row {key!r}: it names no usable folder")
        registry[key] = row
    return registry, content


def load_registry_strict(vault_root):
    """Load a linked-workspace registry in canonical form or fail on corrupt state."""
    return read_registry_strict(vault_root)[0]


def registry_bytes(registry):
    """The exact bytes a registry with these rows is stored as."""
    return (json.dumps({"workspaces": registry}, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def replace_registry(vault_root, registry, *, expected, before_write=None):
    """Write the registry only if the file still holds ``expected`` (``None``: still absent).

    Every writer of the file uses this compare-and-swap, the discipline MCP
    reverse registration already follows through its file plan, so no writer
    can overwrite a row another committed meanwhile and no extra lock order is
    needed. A mismatch raises ``RegistryChangedError`` with nothing written.
    """
    from pathlib import Path
    from _bootstrap.file_transaction import FileChange, FileTransactionError, apply_file_changes

    path = Path(os.path.realpath(os.path.dirname(_registry_path(vault_root)))) / REGISTRY_FILE
    content = registry_bytes(registry)
    if content == expected:
        return
    try:
        apply_file_changes((FileChange(path, expected, content),), before_write=before_write)
    except FileTransactionError as exc:
        if exc.surviving_paths:
            raise
        try:
            changed = read_registry_bytes(vault_root) != expected
        except OSError:
            changed = False
        if changed:
            raise RegistryChangedError(
                "The linked workspace registry changed while this command was updating it; nothing was "
                "written. Run the command again.") from exc
        raise OSError(str(exc)) from exc


def save_registry(vault_root, registry):
    """Write the linked workspace registry unconditionally; only the historical 0.31.0 migration uses it.

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
    been migrated. Returns a dict of slug → {title, status, workspace_mode, tags}.
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
            slug, entry = cached[1], cached[2]
        else:
            try:
                fields = read_frontmatter(fpath)
            except OSError:
                continue
            stem = os.path.splitext(os.path.basename(sub_rel))[0]
            fm_slug = fields.get("key")
            slug = fm_slug if is_valid_key(fm_slug) else stem
            entry = {
                "title": fields.get("title") or stem,
                "status": fields.get("status", ""),
                "workspace_mode": fields.get("workspace_mode", ""),
                "tags": fields.get("tags", []),
                "hub_path": os.path.join(HUB_DIR, sub_rel),
            }
            _hub_metadata_cache[fpath] = (mtime, slug, entry)
        # Shallow-copy on emit so callers can't mutate the cached entry
        # (tags is the only list field, but copy it explicitly).
        result[slug] = {**entry, "tags": list(entry["tags"])}
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

    raise UnknownWorkspaceError(
        f"Unknown workspace '{slug}'. "
        f"No embedded data folder at _Workspaces/{slug}/ "
        f"and no row in this Brain's linked workspace registry (.brain/local/workspaces.json)."
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


def resolve_workspace_strict(vault_root, slug):
    """Resolve one canonical workspace slug against validated registry state."""
    if not is_valid_key(slug):
        raise ValueError(f"Invalid workspace slug: {slug!r}")
    return resolve_workspace(vault_root, slug, registry=load_registry_strict(vault_root))


def list_workspaces_strict(vault_root):
    """List resolvable workspaces once each, failing on invalid registry state."""
    resources = list_workspaces(vault_root, registry=load_registry_strict(vault_root))
    unique = []
    seen = set()
    for resource in resources:
        slug = resource.get("slug")
        if not is_valid_key(slug):
            raise ValueError(f"Invalid embedded workspace slug: {slug!r}")
        if slug in seen:
            continue
        seen.add(slug)
        unique.append(resource)
    return unique


def read_workspace_strict(vault_root, slug):
    """Read one workspace's resolved path and hub metadata by exact slug."""
    if not is_valid_key(slug):
        raise ValueError(f"Invalid workspace slug: {slug!r}")
    match = next(
        (item for item in list_workspaces_strict(vault_root) if item["slug"] == slug),
        None,
    )
    if match is None:
        raise UnknownWorkspaceError(f"Unknown workspace '{slug}'")
    return match


# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------

def register_workspace(vault_root, slug, path, *, before_write=None):
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
    if is_embedded(vault_root, slug):
        raise ValueError(
            f"Cannot register linked workspace '{slug}' — "
            f"an embedded workspace already exists at _Workspaces/{slug}/."
        )

    path = canonical_path(path)

    registry, expected = read_registry_strict(vault_root)
    was_update = slug in registry
    if was_update and registry[slug]["path"] != path:
        from pathlib import Path
        from _bootstrap.mcp_registration import require_no_registered_integrations

        require_no_registered_integrations(Path(vault_root), Path(registry[slug]["path"]))
    registry[slug] = {"path": path}
    if before_write is not None:
        before_write()
    replace_registry(vault_root, registry, expected=expected)

    return {
        "status": "ok",
        "action": "updated" if was_update else "registered",
        "slug": slug,
        "path": path,
        "mode": "linked",
    }


def _staged_registry(plan, vault_root):
    """Read the registry through a file transaction; a damaged file is refused, never rewritten."""
    from pathlib import Path

    path = Path(_registry_path(vault_root))
    content = plan.read_text(path)
    if content is None:
        return path, {}, {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Workspace registry requires explicit recovery: {path}: {exc}") from exc
    workspaces = data.get("workspaces", {}) if isinstance(data, dict) else None
    if not isinstance(workspaces, dict):
        raise ValueError(f"Workspace registry requires explicit recovery: {path}")
    return path, data, workspaces


def _stage(plan, path, data):
    plan.write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def stage_link_row(plan, vault_root, slug, path):
    """Stage the row a manifest naming this Brain implies, inside an MCP file transaction.

    This is the derivation of the registry from a manifest that MCP configuration
    and migration perform (DD-083 item 5): an absent row is added, an equal row is
    kept, and a different row for the slug is a conflict, never overwritten.
    """
    registry_path, data, workspaces = _staged_registry(plan, vault_root)
    row = {"path": canonical_path(path)}
    if slug in workspaces:
        if row_records(salvage_row(slug, workspaces[slug]), path):
            return
        raise ValueError(f"Conflicting MCP reverse registration for {slug}: {registry_path}")
    _stage(plan, registry_path, {**data, "workspaces": {**workspaces, slug: row}})


def stage_canonical_rows(plan, vault_root):
    """Stage rewriting valid rows in canonical form inside an MCP file transaction.

    Rows that name no usable folder are left for the registry repair, which
    keeps a backup when it drops them.
    """
    registry_path, data, workspaces = _staged_registry(plan, vault_root)
    canonical = {slug: salvage_row(slug, value) or value for slug, value in workspaces.items()}
    if canonical != workspaces:
        _stage(plan, registry_path, {**data, "workspaces": canonical})


class Unverified(str, Enum):
    """Why no row of a Brain's registry could be verified."""

    BRAIN_UNREGISTERED = "brain_unregistered"
    VAULT_REGISTRY_UNREADABLE = "vault_registry_unreadable"

    def describe(self):
        return {
            Unverified.BRAIN_UNREGISTERED: ("This Brain is not registered on this machine, so its rows cannot be "
                                            "told from another Brain's and none was verified."),
            Unverified.VAULT_REGISTRY_UNREADABLE: ("This machine's vault registry could not be read, so no linked "
                                                   "workspace row was verified."),
        }[self]


@dataclass(frozen=True)
class RowVerification:
    """One row's verdict: the folder it records and the link classification, read from one manifest snapshot."""

    key: str
    path: str
    classification: LinkClassification


@dataclass(frozen=True)
class RegistryVerification:
    """Every row's verdict, or the reason no row can be verified (and then no rows)."""

    reason: Unverified | None
    rows: tuple[RowVerification, ...]

    def __post_init__(self):
        if self.reason is not None and self.rows:
            raise ValueError("a registry that cannot be verified has no verified rows")


def verify_rows(vault_root, workspaces):
    """Classify each row against the manifest in the folder it records (DD-083 item 6).

    ``workspaces`` holds rows that passed ``salvage_row``. Reads only: one
    classification per row, from one snapshot of its manifests, with plain file
    reads, no lock in, and no write to, any workspace folder. A Brain that is
    not registered on this machine cannot tell its own rows from another
    Brain's, so nothing is verified; nor is anything when the vault registry
    cannot be read.
    """
    from pathlib import Path
    from _bootstrap.workspace_binding import WorkspaceBindingError, classify_link, resolve_local_brain_alias

    root = Path(vault_root)
    try:
        registered = resolve_local_brain_alias(root) is not None
    except WorkspaceBindingError:
        return RegistryVerification(Unverified.VAULT_REGISTRY_UNREADABLE, ())
    if not registered:
        return RegistryVerification(Unverified.BRAIN_UNREGISTERED, ())
    rows = []
    for key, entry in sorted(workspaces.items()):
        folder = Path(canonical_path(entry["path"]))
        rows.append(RowVerification(key, str(folder), classify_link(root, folder, key)))
    return RegistryVerification(None, tuple(rows))


def missing_row_message(slug):
    """The refusal for a slug with no linked workspace registry row."""
    return (f"Workspace '{slug}' is not registered as a linked workspace. "
            f"Only linked workspaces (in .brain/local/workspaces.json) can be unregistered.")


def unregister_workspace(vault_root, slug, *, before_write=None):
    """Remove a linked workspace from .brain/local/workspaces.json.

    Args:
        vault_root: Absolute path to vault root.
        slug: Workspace slug to remove.

    Returns:
        dict with status.

    Raises:
        ValueError: If slug is not in the registry.
    """
    registry, expected = read_registry_strict(vault_root)
    if slug not in registry:
        raise ValueError(missing_row_message(slug))

    from pathlib import Path
    from _bootstrap.mcp_registration import require_no_registered_integrations

    require_no_registered_integrations(Path(vault_root), Path(registry[slug]["path"]))
    del registry[slug]
    if before_write is not None:
        before_write()
    replace_registry(vault_root, registry, expected=expected)

    return {"status": "ok", "action": "unregistered", "slug": slug}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="List and resolve workspaces")
    parser.add_argument("--vault", help="Vault root (auto-detected if omitted)")
    parser.add_argument("--resolve", metavar="SLUG",
                        help="Resolve a workspace slug to its path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    vault_root = str(find_vault_root(args.vault))

    if args.resolve:
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
