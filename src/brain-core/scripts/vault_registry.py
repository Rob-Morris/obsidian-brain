#!/usr/bin/env python3
"""
vault_registry.py — User-home authoritative Brain registry.

Maps symbolic Brain IDs to simple typed locators. Stored as plain text at
$XDG_CONFIG_HOME/brain/vaults (defaulting to ~/.config/brain/vaults),
one entry per line, tab-separated.

Current shipped writer contract is deliberately minimal:

- `local` — `<brain-id>\tlocal\t<absolute-vault-path>`

Legacy two-column entries (`<brain-id>\t<absolute-vault-path>`) are still read
as implicit `local` entries for compatibility. Future non-local kinds may be
preserved opaquely, but this module only resolves/manages local vault paths
today. All per-vault metadata (version, timestamps) lives in each vault's own
`.brain/`.

Usage:
    python3 vault_registry.py --register /path/to/vault
    python3 vault_registry.py --backfill /path/to/vault
    python3 vault_registry.py --unregister /path/to/vault
    python3 vault_registry.py --list [--json]
    python3 vault_registry.py --prune
    python3 vault_registry.py --resolve <brain-id>
"""

import argparse
import contextlib
import fcntl
import json
import os
import sys
from dataclasses import dataclass

from _common import config_home, is_vault_root, random_short_suffix, safe_write, title_to_slug


TYPE_LOCAL = "local"
TYPE_REMOTE = "remote"
KNOWN_KINDS = frozenset({TYPE_LOCAL, TYPE_REMOTE})
STATUS_RESERVED = "reserved"
STATUS_UNKNOWN_KIND = "unknown-kind"

HEADER = "# brain registry v2 — one Brain per line, <brain-id>\\t<kind>\\t<value>\n"


class RegistryReadError(RuntimeError):
    """Raised when the authoritative Brain registry exists but cannot be read."""


@dataclass(frozen=True)
class RegistryEntry:
    brain_id: str
    kind: str
    value: str


def _registry_path():
    return os.fspath(config_home() / "brain" / "vaults")


@contextlib.contextmanager
def _locked():
    """Serialize load-modify-save across concurrent installers.

    Locks a sibling ``.lock`` file (not the registry itself) so locking works
    before the registry has been created. ``load_registry_entries()`` on its
    own is intentionally unlocked — best-effort reads must not block installer
    prompts on a concurrent writer.
    """
    lock_path = _registry_path() + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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


def register(vault_path):
    """Register a local vault. Returns the resolved Brain ID.

    - Brain ID = slugified basename.
    - If path already registered (under any Brain ID), returns existing ID (no-op).
    - On basename collision with a different path, appends random [a-z0-9]{3} suffix.
    """
    abs_path = _absolute(vault_path)
    with _locked():
        entries = load_registry_entries()
        existing = _find_local_brain_id_by_path(entries, abs_path)
        if existing is not None:
            return existing
        base_brain_id = title_to_slug(os.path.basename(abs_path)) or "vault"
        brain_id = base_brain_id
        while brain_id in entries:
            brain_id = f"{base_brain_id}-{random_short_suffix()}"
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
    """Remove the local entry keyed to this path. Returns True if removed."""
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
        return True


def resolve(brain_id):
    """Return the absolute local vault path for a Brain ID, or None."""
    entry = load_registry_entries().get(brain_id)
    if entry is None or entry.kind != TYPE_LOCAL:
        return None
    return entry.value


def list_entries():
    """Return sorted registry entries with honest local-vs-non-local detail."""
    rendered = []
    for brain_id, entry in sorted(load_registry_entries().items()):
        if entry.kind == TYPE_LOCAL:
            rendered.append(
                {
                    "alias": brain_id,
                    "kind": entry.kind,
                    "value": entry.value,
                    "stale": not is_vault_root(entry.value),
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
            }
        )
    return rendered


def prune():
    """Remove stale local entries. Returns list of removed Brain IDs."""
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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        if args.register:
            print(register(args.register))
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
                    if entry["kind"] == TYPE_LOCAL:
                        stale_tag = " (stale)" if entry["stale"] else ""
                        print(f"  {entry['alias']} [{entry['kind']}]: {entry['value']}{stale_tag}")
                        continue
                    note = " (reserved; unresolved here)"
                    if entry["status"] == STATUS_UNKNOWN_KIND:
                        note = " (unrecognised kind; unresolved here)"
                    print(f"  {entry['alias']} [{entry['kind']}]: {entry['value']}{note}")
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
    except RegistryReadError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
