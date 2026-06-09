#!/usr/bin/env python3
"""
vault_registry.py — User-home authoritative Brain registry.

Maps symbolic Brain IDs to simple typed locators. Stored as plain text at
$XDG_CONFIG_HOME/brain/vaults (defaulting to ~/.config/brain/vaults),
one entry per line, tab-separated.

This is a machine-level surface, not a vault-scoped repair.py helper. It owns
user-home registry rows and the default Brain pointer; vault-local repair must
not import it to mutate machine-level state.

Current shipped writer contract is deliberately minimal:

- `local` — `<brain-id>\tlocal\t<absolute-vault-path>`

Legacy two-column entries (`<brain-id>\t<absolute-vault-path>`) are still read
as implicit `local` entries for compatibility. Future non-local kinds may be
preserved opaquely, but this module only resolves/manages local vault paths
today. All per-vault metadata (version, timestamps) lives in each vault's own
`.brain/`.

The machine-wide default Brain ID is stored separately at
$XDG_CONFIG_HOME/brain/default (a single line — the Brain ID). It never
forms part of the vaults row format.

Usage:
    python3 vault_registry.py --register /path/to/vault
    python3 vault_registry.py --register /path/to/vault --id my-brain
    python3 vault_registry.py --backfill /path/to/vault
    python3 vault_registry.py --unregister /path/to/vault
    python3 vault_registry.py --list [--json]
    python3 vault_registry.py --prune
    python3 vault_registry.py --resolve <brain-id>
    python3 vault_registry.py --set-default <brain-id>
    python3 vault_registry.py --get-default
    python3 vault_registry.py --clear-default
"""

import argparse
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass

from _common import config_home, is_vault_root, random_short_suffix, safe_write, title_to_slug
from _common._file_lock import exclusive_file_lock


TYPE_LOCAL = "local"
TYPE_REMOTE = "remote"
KNOWN_KINDS = frozenset({TYPE_LOCAL, TYPE_REMOTE})
STATUS_RESERVED = "reserved"
STATUS_UNKNOWN_KIND = "unknown-kind"

HEADER = "# brain registry v2 — one Brain per line, <brain-id>\\t<kind>\\t<value>\n"

_BRAIN_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RegistryReadError(RuntimeError):
    """Raised when the authoritative Brain registry exists but cannot be read."""


class RegistryConflictError(ValueError):
    """Raised when a register or set-default call would create a conflict."""


@dataclass(frozen=True)
class RegistryEntry:
    brain_id: str
    kind: str
    value: str


def _registry_path():
    return os.fspath(config_home() / "brain" / "vaults")


def _default_path():
    return os.fspath(config_home() / "brain" / "default")


@contextlib.contextmanager
def _locked():
    """Serialize load-modify-save across concurrent installers.

    Locks a sibling ``.lock`` file (not the registry itself) so locking works
    before the registry has been created. ``load_registry_entries()`` on its
    own is intentionally unlocked — best-effort reads must not block installer
    prompts on a concurrent writer.
    """
    lock_path = _registry_path() + ".lock"
    with exclusive_file_lock(lock_path):
        yield


def _write_default_unlocked(brain_id):
    """Write the default Brain ID to the default file without locking.

    Must only be called from within an existing _locked() block.
    """
    safe_write(_default_path(), brain_id + "\n")


def _clear_default_unlocked():
    """Remove the default file without locking. Tolerates absence.

    Must only be called from within an existing _locked() block.
    """
    try:
        os.unlink(_default_path())
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RegistryReadError(
            f"could not remove default Brain pointer at {_default_path()}: {exc}"
        ) from exc


def get_default():
    """Return the stored default Brain ID, or None.

    Best-effort unlocked read — mirrors load_registry_entries().
    Missing file or empty content returns None.
    A real OS error is wrapped in RegistryReadError.
    The returned id is returned as-is; staleness classification belongs in
    Phase 2 resolution.
    """
    path = _default_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            brain_id = handle.read().strip()
        return brain_id if brain_id else None
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RegistryReadError(
            f"could not read default Brain pointer at {path}: {exc}"
        ) from exc


def set_default(brain_id):
    """Set the machine default Brain ID.

    Validates that brain_id is a known LOCAL entry (raises RegistryConflictError
    otherwise) then atomically writes the default file.  Runs under _locked()
    to keep validation and write atomic.
    """
    with _locked():
        entries = load_registry_entries()
        entry = entries.get(brain_id)
        if entry is None or entry.kind != TYPE_LOCAL:
            raise RegistryConflictError(
                f"cannot set default: '{brain_id}' is not a registered local Brain; "
                f"register it first"
            )
        _write_default_unlocked(brain_id)


def clear_default():
    """Remove the default Brain pointer.  Tolerates absence."""
    with _locked():
        _clear_default_unlocked()


def _parse_entry(raw: str) -> RegistryEntry | None:
    fields = [field.strip() for field in raw.split("\t")]
    if len(fields) == 2:
        brain_id, value = fields
        kind = TYPE_LOCAL
    elif len(fields) == 3:
        brain_id, kind, value = fields
    else:
        return None
    if not brain_id or not kind or not value:
        return None
    return RegistryEntry(brain_id=brain_id, kind=kind, value=value)


def load_registry_entries():
    """Load all registry entries keyed by Brain ID.

    Missing file → {}.
    Malformed lines are skipped with a stderr warning.
    Unrecognised kinds are preserved opaquely and warned once per read.
    """
    path = _registry_path()
    result = {}
    malformed = 0
    unknown_kinds: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                entry = _parse_entry(raw)
                if entry is None:
                    malformed += 1
                    continue
                if entry.kind not in KNOWN_KINDS:
                    unknown_kinds.add(entry.kind)
                result[entry.brain_id] = entry
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RegistryReadError(f"could not read brain registry at {path}: {exc}") from exc
    if malformed:
        print(
            f"vault_registry: skipping {malformed} malformed line(s) in {path}",
            file=sys.stderr,
        )
    if unknown_kinds:
        kinds = ", ".join(sorted(unknown_kinds))
        print(
            f"vault_registry: unrecognised kind(s) in {path}: {kinds}",
            file=sys.stderr,
        )
    return result


def _save_registry_entries(entries):
    """Write typed registry entries atomically."""
    lines = [HEADER]
    for brain_id in sorted(entries):
        entry = entries[brain_id]
        lines.append(f"{brain_id}\t{entry.kind}\t{entry.value}\n")
    safe_write(_registry_path(), "".join(lines))


def _absolute(vault_path):
    """Canonicalize a vault path for registry storage.

    Uses ``realpath`` so paths reaching the same vault via different symlinks
    collapse to one entry — keeps register/unregister idempotent. Note this
    diverges from ``install.sh``'s ``resolve_path`` (which uses ``cd && pwd``
    and doesn't follow symlinks), so the installer may display a different
    path string than what's stored. Cosmetic only.
    """
    path = os.path.expanduser(vault_path)
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.realpath(path)


def _local_entries(entries):
    return {
        brain_id: entry
        for brain_id, entry in entries.items()
        if entry.kind == TYPE_LOCAL
    }


def _find_local_brain_id_by_path(entries, abs_path):
    """Return the local Brain ID mapping to abs_path, or None."""
    for brain_id, entry in _local_entries(entries).items():
        if entry.value == abs_path:
            return brain_id
    return None


def _is_valid_brain_id(brain_id):
    """Return True when brain_id is a valid slug (lowercase alphanumeric and hyphens)."""
    return bool(brain_id and _BRAIN_ID_RE.fullmatch(brain_id))


def register(vault_path, brain_id=None):
    """Register a local vault. Returns the resolved Brain ID.

    When brain_id is None (default):
    - Brain ID = slugified basename.
    - If path already registered (under any Brain ID), returns existing ID (no-op).
    - On basename collision with a different path, appends random [a-z0-9]{3} suffix.

    When brain_id is given:
    - Must be a valid slug (lowercase alphanumeric and hyphens).
    - brain_id maps to THIS path → no-op, return brain_id.
    - brain_id maps to a DIFFERENT path → raises RegistryConflictError.
    - brain_id free, path unregistered → create brain_id → path, return brain_id.
    - brain_id free, path already registered under X → raises RegistryConflictError.
      (Re-keying is not supported; use Phase-3 rename primitives instead.)
    """
    abs_path = _absolute(vault_path)
    if brain_id is not None and not _is_valid_brain_id(brain_id):
        raise ValueError(
            f"invalid Brain ID {brain_id!r}: must match ^[a-z0-9]+(-[a-z0-9]+)*$"
        )
    with _locked():
        entries = load_registry_entries()
        existing_id = _find_local_brain_id_by_path(entries, abs_path)

        if brain_id is None:
            # Original behaviour — auto-assign from basename.
            if existing_id is not None:
                return existing_id
            base_brain_id = title_to_slug(os.path.basename(abs_path)) or "vault"
            new_id = base_brain_id
            while new_id in entries:
                new_id = f"{base_brain_id}-{random_short_suffix()}"
            entries[new_id] = RegistryEntry(
                brain_id=new_id,
                kind=TYPE_LOCAL,
                value=abs_path,
            )
            _save_registry_entries(entries)
            return new_id

        # Explicit brain_id given.
        existing_entry = entries.get(brain_id)
        if existing_entry is not None:
            # ID is taken — check whether it points to this path.
            if existing_entry.kind == TYPE_LOCAL and existing_entry.value == abs_path:
                return brain_id  # exact no-op
            raise RegistryConflictError(
                f"Brain ID '{brain_id}' is already registered to a different path: "
                f"{existing_entry.value!r}; unregister it first"
            )
        # ID is free.
        if existing_id is not None:
            raise RegistryConflictError(
                f"path {abs_path!r} is already registered as '{existing_id}'; "
                f"unregister it first or pass brain_id='{existing_id}'"
            )
        entries[brain_id] = RegistryEntry(
            brain_id=brain_id,
            kind=TYPE_LOCAL,
            value=abs_path,
        )
        _save_registry_entries(entries)
        return brain_id


def backfill(vault_path):
    """Register the vault if absent.

    Equivalent to register() since register already no-ops when the path is
    already known; kept as a named entry point for upgrade/install intent.
    """
    return register(vault_path)


def unregister(vault_path):
    """Remove the local entry keyed to this path. Returns True if removed.

    When the removed Brain ID matches the stored default, the default pointer
    is cleared atomically within the same lock.
    """
    abs_path = _absolute(vault_path)
    with _locked():
        entries = load_registry_entries()
        to_remove = [
            brain_id
            for brain_id, entry in _local_entries(entries).items()
            if entry.value == abs_path
        ]
        if not to_remove:
            return False
        for brain_id in to_remove:
            del entries[brain_id]
        _save_registry_entries(entries)
        # Clear a dangling default pointer within the same lock (get_default is
        # an unlocked read — safe inside the lock as it never acquires it).
        current_default = get_default()
        if current_default is not None and current_default in to_remove:
            _clear_default_unlocked()
        return True


def resolve(brain_id):
    """Return the absolute local vault path for a Brain ID, or None."""
    entry = load_registry_entries().get(brain_id)
    if entry is None or entry.kind != TYPE_LOCAL:
        return None
    return entry.value


def list_entries():
    """Return sorted registry entries with honest local-vs-non-local detail.

    Each entry dict includes a boolean ``default`` key indicating whether
    it is the current machine default.
    """
    default_id = get_default()
    rendered = []
    for brain_id, entry in sorted(load_registry_entries().items()):
        is_default = brain_id == default_id
        if entry.kind == TYPE_LOCAL:
            rendered.append(
                {
                    "alias": brain_id,
                    "kind": entry.kind,
                    "value": entry.value,
                    "stale": not is_vault_root(entry.value),
                    "default": is_default,
                }
            )
            continue
        status = STATUS_RESERVED if entry.kind == TYPE_REMOTE else STATUS_UNKNOWN_KIND
        rendered.append(
            {
                "alias": brain_id,
                "kind": entry.kind,
                "value": entry.value,
                "stale": None,
                "status": status,
                "default": is_default,
            }
        )
    return rendered


def prune():
    """Remove stale local entries. Returns list of removed Brain IDs.

    When the stored default points at a pruned Brain ID, the default pointer is
    cleared atomically within the same lock — mirroring unregister(), so the two
    removal paths keep the "no dangling default" invariant consistently.
    """
    with _locked():
        entries = load_registry_entries()
        stale = [
            brain_id
            for brain_id, entry in _local_entries(entries).items()
            if not is_vault_root(entry.value)
        ]
        if not stale:
            return []
        for brain_id in stale:
            del entries[brain_id]
        _save_registry_entries(entries)
        # Clear a dangling default pointer within the same lock (get_default is
        # an unlocked read — safe inside the lock as it never acquires it).
        current_default = get_default()
        if current_default is not None and current_default in stale:
            _clear_default_unlocked()
        return stale


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="User-home authoritative Brain registry")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", metavar="PATH")
    group.add_argument("--backfill", metavar="PATH")
    group.add_argument("--unregister", metavar="PATH")
    group.add_argument("--list", action="store_true")
    group.add_argument("--prune", action="store_true")
    group.add_argument("--resolve", metavar="BRAIN_ID")
    group.add_argument("--set-default", metavar="BRAIN_ID", dest="set_default")
    group.add_argument("--get-default", action="store_true", dest="get_default")
    group.add_argument("--clear-default", action="store_true", dest="clear_default")
    parser.add_argument("--json", action="store_true")
    # --id modifies --register only; not part of the mutually-exclusive group.
    parser.add_argument("--id", metavar="ID", dest="id")
    args = parser.parse_args()

    try:
        if args.register:
            print(register(args.register, brain_id=args.id))
        elif args.backfill:
            print(backfill(args.backfill))
        elif args.unregister:
            unregister(args.unregister)  # best-effort; always exit 0
        elif args.list:
            entries = list_entries()
            if args.json:
                print(json.dumps(entries, indent=2))
            elif not entries:
                print("No Brains registered.")
            else:
                for entry in entries:
                    default_tag = " (default)" if entry.get("default") else ""
                    if entry["kind"] == TYPE_LOCAL:
                        stale_tag = " (stale)" if entry["stale"] else ""
                        print(f"  {entry['alias']} [{entry['kind']}]: {entry['value']}{stale_tag}{default_tag}")
                        continue
                    note = " (reserved; unresolved here)"
                    if entry["status"] == STATUS_UNKNOWN_KIND:
                        note = " (unrecognised kind; unresolved here)"
                    print(f"  {entry['alias']} [{entry['kind']}]: {entry['value']}{note}{default_tag}")
        elif args.prune:
            removed = prune()
            if not removed:
                print("No stale entries.")
            else:
                for brain_id in removed:
                    print(f"Removed: {brain_id}")
        elif args.resolve:
            path = resolve(args.resolve)
            if path is None:
                print(f"Unknown Brain ID: {args.resolve}", file=sys.stderr)
                sys.exit(1)
            print(path)
        elif args.set_default:
            set_default(args.set_default)
        elif args.get_default:
            brain_id = get_default()
            if brain_id is not None:
                print(brain_id)
        elif args.clear_default:
            clear_default()
    except (RegistryReadError, RegistryConflictError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
