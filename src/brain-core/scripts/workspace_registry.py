#!/usr/bin/env python3
"""Canonical workspace metadata and derived, path-keyed local registrations.

Folders declare binding authority in their manifests. This inventory records
known locations; reading a canonical workspace does not select a local folder.
Historical raw-load/save APIs retain the released migration's slug-keyed format.
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
from _common._slugs import is_valid_key
from _common._vault import find_vault_root

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
    return os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(path))))


def salvage_row(key, entry):
    """Validate a current path row or convert one historical slug row losslessly."""
    path = entry if isinstance(entry, str) else entry.get("path") if isinstance(entry, dict) else None
    slug = entry.get("slug", key) if isinstance(entry, dict) else key
    if not is_valid_key(slug) or not isinstance(path, str) or not path or "\0" in path:
        return None
    if not os.path.isabs(os.path.expanduser(path)):
        return None
    return {"slug": slug, "path": canonical_path(path)}


def registry_rows(data):
    """Decode current or legacy inventory without dropping invalid or conflicting rows."""
    rows, invalid = inspect_registry_rows(data)
    if invalid:
        raise ValueError(f"Invalid linked workspace registry rows {invalid!r}: names no usable folder")
    return rows


def inspect_registry_rows(data):
    """Separate salvageable rows from invalid rows; contradictory identity always refuses."""
    if not isinstance(data, dict):
        raise ValueError("Invalid linked workspace registry: expected an object")
    current = "schema_version" in data or "registrations" in data
    if current:
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported workspace registry schema; use a compatible runtime")
        if "workspaces" in data:
            raise ValueError("Conflicting legacy and current workspace registry collections")
    if current and "registrations" not in data:
        raise ValueError("Invalid workspace registry: current schema requires registrations")
    raw = data.get("registrations" if current else "workspaces", {})
    if not isinstance(raw, dict):
        raise ValueError("Invalid linked workspace registry: registrations must be an object")
    rows = {}
    invalid = []
    for key, entry in raw.items():
        if current and (not isinstance(entry, dict) or "slug" not in entry
                        or not os.path.isabs(key) or key != entry.get("path")):
            invalid.append(key)
            continue
        row = salvage_row(key, entry)
        if row is None:
            invalid.append(key)
            continue
        path = row["path"]
        if path in rows and rows[path] != row:
            raise ValueError(f"Conflicting workspace registrations for {path}")
        rows[path] = row
    return rows, invalid


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


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate workspace registry field: {key!r}")
        result[key] = value
    return result


def decode_registry(content):
    """Validate an inventory's exact bytes and return path-keyed registrations."""
    try:
        data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid linked workspace registry: {exc}") from exc
    return registry_rows(data)


def load_registry(vault_root):
    """Read registrations, refusing corrupt inventory rather than silently losing rows."""
    return load_registry_strict(vault_root)


def load_raw_registry(vault_root):
    """Every stored entry as written, unvalidated: only the historical 0.31.0 key migration reads this.

    Its keys may predate the key contract, which is what that migration
    remaps; no caller may resolve these paths. ``{}`` when absent or unreadable.
    """
    try:
        content = read_registry_bytes(vault_root)
        data = {} if content is None else json.loads(content.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("workspaces", {}), dict):
            return {}
        if "schema_version" in data or "registrations" in data:
            raise RuntimeError("Historical workspace key migration cannot rewrite the current multi-folder inventory")
        workspaces = data.get("workspaces", {})
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
    registry = decode_registry(content)
    if canonical_path(vault_root) in registry:
        raise ValueError("Vault-self is not a workspace registration; remove the stale row explicitly")
    return registry, content


def load_registry_strict(vault_root):
    """Load a linked-workspace registry in canonical form or fail on corrupt state."""
    return read_registry_strict(vault_root)[0]


def registry_bytes(registry):
    """The exact bytes a registry with these rows is stored as."""
    data = {"schema_version": 1, "registrations": registry}
    data["registrations"] = registry_rows(data)
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


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
        if slug in result:
            raise ValueError(f"Duplicate canonical workspace hub key: {slug!r}")
        result[slug] = {**entry, "tags": list(entry["tags"])}
    for stale in set(_hub_metadata_cache) - seen:
        del _hub_metadata_cache[stale]
    return result


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def workspace_locations(vault_root, slug, registry=None):
    """Known locations for an existing canonical workspace, without selecting one."""
    if not is_valid_key(slug):
        raise ValueError(f"Invalid workspace slug: {slug!r}")
    if slug not in _scan_hub_metadata(vault_root):
        raise UnknownWorkspaceError(f"Unknown canonical workspace '{slug}'")
    registry = load_registry_strict(vault_root) if registry is None else registry
    return _workspace_locations(vault_root, slug, (row for row in registry.values() if row["slug"] == slug))


def _workspace_locations(vault_root, slug, rows):
    """Collect one hub's locations from its already selected registrations."""
    locations = [{**row, "mode": "linked"} for row in rows]
    embedded = os.path.join(vault_root, EMBEDDED_DATA_DIR, slug)
    if os.path.isdir(embedded):
        path = canonical_path(embedded)
        locations = [row for row in locations if row["path"] != path]
        locations.insert(0, {"slug": slug, "path": path, "mode": "embedded"})
    return sorted(locations, key=lambda row: row["path"])


def resolve_workspace(vault_root, slug, registry=None, *, folder=None):
    """Resolve an explicitly selected folder; never pick one from a shared slug."""
    locations = workspace_locations(vault_root, slug, registry)
    if folder is not None:
        match = next((row for row in locations if row["path"] == canonical_path(folder)), None)
        if match is not None:
            return match
        raise ValueError(f"Folder {folder!s} is not a known location for workspace '{slug}'")
    raise ValueError(f"Workspace '{slug}' needs an explicit folder selection; candidates: "
                     + ", ".join(row["path"] for row in locations))


def list_workspaces(vault_root, registry=None):
    """Canonical hubs with zero or more locations; recorded paths are not access authority."""
    registry = load_registry_strict(vault_root) if registry is None else registry
    rows_by_slug = {}
    for row in registry.values():
        rows_by_slug.setdefault(row["slug"], []).append(row)
    result = []
    for slug, meta in sorted(_scan_hub_metadata(vault_root).items()):
        if not is_valid_key(slug):
            raise ValueError(f"Invalid canonical workspace slug: {slug!r}")
        result.append({"slug": slug, **meta, "locations": _workspace_locations(vault_root, slug, rows_by_slug.get(slug, ()))})
    return result


def resolve_workspace_strict(vault_root, slug, *, folder=None):
    """Resolve one explicitly selected location against validated registry state."""
    return resolve_workspace(vault_root, slug, registry=load_registry_strict(vault_root), folder=folder)


def list_workspaces_strict(vault_root):
    """List canonical workspaces without choosing arbitrary folders."""
    return list_workspaces(vault_root, registry=load_registry_strict(vault_root))


def read_workspace_strict(vault_root, slug):
    """Read canonical metadata and its zero/one/many known locations."""
    if not is_valid_key(slug):
        raise ValueError(f"Invalid workspace slug: {slug!r}")
    registry = load_registry_strict(vault_root)
    meta = _scan_hub_metadata(vault_root).get(slug)
    if meta is None:
        raise UnknownWorkspaceError(f"Unknown workspace '{slug}'")
    rows = (row for row in registry.values() if row["slug"] == slug)
    return {"slug": slug, **meta, "locations": _workspace_locations(vault_root, slug, rows)}


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
        ValueError: If the selected folder already records a different identity.
    """
    if not is_valid_key(slug):
        raise ValueError(f"Invalid workspace slug: {slug!r}")
    path = canonical_path(path)
    if path == canonical_path(vault_root):
        raise ValueError("Vault-self is not a workspace registration")
    registry, expected = read_registry_strict(vault_root)
    row = {"slug": slug, "path": path}
    previous = registry.get(path)
    if previous is not None and previous != row:
        raise ValueError(f"Folder {path} already records workspace '{previous['slug']}'; use workspace.rebind")
    registry[path] = row
    replace_registry(vault_root, registry, expected=expected, before_write=before_write)
    return {"status": "ok", "action": "unchanged" if previous else "registered",
            "slug": slug, "path": path, "mode": "linked"}


def _staged_registry(plan, vault_root):
    """Observe and validate inventory through the existing file transaction."""
    from pathlib import Path

    path = Path(_registry_path(vault_root))
    content = plan.read_bytes(path)
    return path, {} if content is None else decode_registry(content)


def stage_link_row(plan, vault_root, slug, path):
    """Derive only the selected manifest's path row, preserving all other folders."""
    registry_path, rows = _staged_registry(plan, vault_root)
    path = canonical_path(path)
    if path == canonical_path(vault_root):
        raise ValueError("Vault-self is not a workspace registration")
    row = salvage_row(path, {"slug": slug, "path": path})
    if row is None:
        raise ValueError(f"Invalid workspace registration for {path}")
    if path in rows and rows[path] != row:
        raise ValueError(f"Conflicting MCP reverse registration for {path}: {registry_path}")
    plan.write_text(registry_path, registry_bytes({**rows, path: row}).decode("utf-8"))


def stage_canonical_rows(plan, vault_root):
    """Losslessly convert supported historical rows within the MCP file transaction."""
    path, rows = _staged_registry(plan, vault_root)
    if canonical_path(vault_root) in rows:
        raise ValueError("Vault-self is not a workspace registration; remove its stale row explicitly")
    if plan.read_bytes(path) is not None:
        plan.write_text(path, registry_bytes(rows).decode("utf-8"))


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
        rows.append(RowVerification(key, str(folder), classify_link(root, folder, entry["slug"])))
    return RegistryVerification(None, tuple(rows))


def missing_row_message(slug):
    """The refusal for a slug with no linked workspace registry row."""
    return (f"Workspace '{slug}' is not registered as a linked workspace. "
            f"Only linked workspaces (in .brain/local/workspaces.json) can be unregistered.")


def unregister_workspace(vault_root, path, *, slug=None, before_write=None):
    """Remove only one explicitly selected folder row after integration guards."""
    path = canonical_path(path)
    registry, expected = read_registry_strict(vault_root)
    row = registry.get(path)
    if row is None or (slug is not None and slug != row["slug"]):
        raise ValueError(f"No matching workspace registration at {path}")
    from pathlib import Path
    from _bootstrap.mcp_registration import require_no_registered_integrations

    require_no_registered_integrations(Path(vault_root), Path(path))
    del registry[path]
    replace_registry(vault_root, registry, expected=expected, before_write=before_write)
    return {"status": "ok", "action": "unregistered", "slug": row["slug"], "path": path}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="List and resolve workspaces")
    parser.add_argument("--vault", help="Vault root (auto-detected if omitted)")
    parser.add_argument("--resolve", metavar="SLUG",
                        help="Resolve a workspace slug to its path")
    parser.add_argument("--folder", help="Explicit absolute folder selection for resolution")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    vault_root = str(find_vault_root(args.vault))

    if args.resolve:
        try:
            result = resolve_workspace(vault_root, args.resolve, folder=args.folder)
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
                    print(f"  {ws['slug']}: " + ", ".join(row["path"] for row in ws["locations"]))


if __name__ == "__main__":
    main()
