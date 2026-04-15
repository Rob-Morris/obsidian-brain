#!/usr/bin/env python3
"""
vault_registry.py — User-home registry of installed brain vaults.

Maps aliases to absolute vault paths. Stored as plain text at
$XDG_CONFIG_HOME/brain/vaults (defaulting to ~/.config/brain/vaults),
one entry per line, tab-separated.

Schema is deliberately minimal: alias + path only. All per-vault
metadata (version, timestamps) lives in each vault's own .brain/.

Usage:
    python3 vault_registry.py --register /path/to/vault
    python3 vault_registry.py --backfill /path/to/vault
    python3 vault_registry.py --unregister /path/to/vault
    python3 vault_registry.py --list [--json]
    python3 vault_registry.py --prune
    python3 vault_registry.py --resolve <alias>
"""

import argparse
import json
import os
import sys
from pathlib import Path

from _common import is_vault_root, random_short_suffix, safe_write, title_to_slug


HEADER = "# brain vault registry — one vault per line, <alias>\\t<absolute-path>\n"


def _config_home():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return xdg
    home = os.environ.get("HOME") or str(Path.home())
    return os.path.join(home, ".config")


def _registry_path():
    return os.path.join(_config_home(), "brain", "vaults")


def load():
    """Load the registry. Returns dict of alias → absolute path.

    Missing file → {}.
    Malformed lines are skipped with a stderr warning.
    """
    path = _registry_path()
    result = {}
    malformed = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "\t" not in s:
                    malformed += 1
                    continue
                alias, _, vault_path = s.partition("\t")
                alias, vault_path = alias.strip(), vault_path.strip()
                if alias and vault_path:
                    result[alias] = vault_path
                else:
                    malformed += 1
    except FileNotFoundError:
        return {}
    except OSError as e:
        print(f"vault_registry: error reading {path}: {e}", file=sys.stderr)
        return {}
    if malformed:
        print(
            f"vault_registry: skipping {malformed} malformed line(s) in {path}",
            file=sys.stderr,
        )
    return result


def save(registry):
    """Write the registry atomically."""
    lines = [HEADER]
    for alias in sorted(registry):
        lines.append(f"{alias}\t{registry[alias]}\n")
    safe_write(_registry_path(), "".join(lines))


def _absolute(vault_path):
    """Resolve to absolute real path, expanding ~, cwd-relative, and symlinks.

    Uses ``realpath`` so entries stay consistent with install.sh's ``cd && pwd``
    resolution — otherwise a user registering a vault via a symlink and later
    unregistering via the real path (or vice versa) would see mismatches.
    ``realpath`` handles non-existent paths gracefully by resolving whatever
    prefix does exist.
    """
    p = os.path.expanduser(vault_path)
    if not os.path.isabs(p):
        p = os.path.join(os.getcwd(), p)
    return os.path.realpath(p)


def _find_alias_by_path(registry, abs_path):
    """Return the alias mapping to abs_path, or None."""
    for alias, stored in registry.items():
        if stored == abs_path:
            return alias
    return None


def register(vault_path):
    """Register a vault. Returns the resolved alias.

    - Alias = slugified basename.
    - If path already registered (under any alias), returns existing alias (no-op).
    - On basename collision with a different path, appends random [a-z0-9]{3} suffix.
    """
    abs_path = _absolute(vault_path)
    registry = load()
    existing = _find_alias_by_path(registry, abs_path)
    if existing is not None:
        return existing
    base_alias = title_to_slug(os.path.basename(abs_path)) or "vault"
    alias = base_alias
    while alias in registry and registry[alias] != abs_path:
        alias = f"{base_alias}-{random_short_suffix()}"
    registry[alias] = abs_path
    save(registry)
    return alias


def backfill(vault_path):
    """Register the vault if absent. Equivalent to register() since register
    already no-ops when the path is already known; kept as a named entry point
    for intent ("I'm upgrading, make sure it's tracked") and CLI clarity.
    """
    return register(vault_path)


def unregister(vault_path):
    """Remove the entry keyed to this path. Returns True if removed."""
    abs_path = _absolute(vault_path)
    registry = load()
    to_remove = [a for a, p in registry.items() if p == abs_path]
    if not to_remove:
        return False
    for a in to_remove:
        del registry[a]
    save(registry)
    return True


def resolve(alias):
    """Return the absolute path for an alias, or None if not registered."""
    return load().get(alias)


def list_entries():
    """Return [{alias, path, stale}, ...] sorted by alias."""
    registry = load()
    return [
        {"alias": a, "path": registry[a], "stale": not is_vault_root(registry[a])}
        for a in sorted(registry)
    ]


def prune():
    """Remove stale entries. Returns list of removed aliases."""
    registry = load()
    stale = [a for a, p in registry.items() if not is_vault_root(p)]
    if not stale:
        return []
    for a in stale:
        del registry[a]
    save(registry)
    return stale


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="User-home vault registry")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--register", metavar="PATH")
    g.add_argument("--backfill", metavar="PATH")
    g.add_argument("--unregister", metavar="PATH")
    g.add_argument("--list", action="store_true")
    g.add_argument("--prune", action="store_true")
    g.add_argument("--resolve", metavar="ALIAS")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

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
        else:
            if not entries:
                print("No vaults registered.")
            else:
                for e in entries:
                    tag = " (stale)" if e["stale"] else ""
                    print(f"  {e['alias']}: {e['path']}{tag}")
    elif args.prune:
        removed = prune()
        if not removed:
            print("No stale entries.")
        else:
            for a in removed:
                print(f"Removed: {a}")
    elif args.resolve:
        path = resolve(args.resolve)
        if path is None:
            print(f"Unknown alias: {args.resolve}", file=sys.stderr)
            sys.exit(1)
        print(path)


if __name__ == "__main__":
    main()
