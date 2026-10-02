"""Machine-level Brain discovery over the vault registry and the current vault.

The vault registry (``vault_registry``) is the one home for which local Brains
exist on this machine. Discovery reads it and the current vault; nothing is
cached or written.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from _common import is_vault_root
from _common._vault import is_brain_vault
import vault_registry


def _canonical_brain_path(path: str | Path) -> Path:
    expanded = os.path.expanduser(str(path))
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return Path(os.path.realpath(expanded))


def _brain_entry(path: Path, *, alias: str | None = None, source: str) -> dict[str, Any]:
    return {
        "alias": alias,
        "path": str(path),
        "sources": [source],
        "stale": False,
    }


def _merge_brain_entry(
    discovered: dict[str, dict[str, Any]],
    path: Path,
    *,
    alias: str | None,
    source: str,
) -> None:
    record = discovered.setdefault(
        str(path),
        _brain_entry(
            path,
            alias=alias,
            source=source,
        ),
    )
    if source not in record["sources"]:
        record["sources"].append(source)
    if record["alias"] is None and alias is not None:
        record["alias"] = alias


def discover_brains(
    *,
    current_vault: str | Path | None = None,
) -> dict[str, Any]:
    """Discover Brains from the vault registry and the current vault.

    A Brain whose only source is ``current`` is listed under
    ``unregistered_brains``: registering it is a person's choice, never a
    side effect of discovery.
    """
    discovered: dict[str, dict[str, Any]] = {}

    if current_vault is not None:
        current_path = _canonical_brain_path(current_vault)
        if is_vault_root(current_path):
            _merge_brain_entry(
                discovered,
                current_path,
                alias=None,
                source="current",
            )

    stale_registry_entries: list[dict[str, str]] = []
    for entry in vault_registry.list_entries():
        if entry.get("kind") != vault_registry.TYPE_LOCAL:
            continue
        registry_path = _canonical_brain_path(entry["value"])
        # The same narrow predicate as resolution and the MCP inventory: a registered Brain is present
        # only with its .brain-core/VERSION, so a row is never a Brain here and absent there.
        if entry["stale"] or not is_brain_vault(registry_path):
            stale_registry_entries.append(
                {"alias": entry["alias"], "path": str(registry_path)}
            )
            continue

        _merge_brain_entry(
            discovered,
            registry_path,
            alias=entry["alias"],
            source="vault_registry",
        )

    brains = sorted(
        discovered.values(),
        key=lambda item: ((item["alias"] or ""), item["path"]),
    )
    stale_registry_entries.sort(key=lambda item: (item["alias"], item["path"]))
    return {
        "brains": brains,
        "stale_registry_entries": stale_registry_entries,
        "unregistered_brains": [
            brain["path"] for brain in brains if brain["sources"] == ["current"]
        ],
        "registry": {
            "path": str(vault_registry.registry_path()),
            "brains_count": sum("vault_registry" in brain["sources"] for brain in brains),
            "stale": bool(stale_registry_entries),
        },
    }
