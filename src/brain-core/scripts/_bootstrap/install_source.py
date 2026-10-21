"""Persist selected Core provenance independently of the shared CLI bundle."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from _bootstrap.file_transaction import FilePlan, apply_file_changes


def read_source(source_root: Path, *, core: Path | None = None) -> dict:
    """Read acquisition evidence, or identify local source without claiming publication."""
    version = ((core or source_root / "src/brain-core") / "VERSION").read_text(encoding="utf-8").strip()
    receipt = source_root / ".brain-source-receipt.json"
    if not receipt.exists():
        return {"schema": "brain.install-source/1", "kind": "local", "version": version}
    value = json.loads(receipt.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "brain.source-acquisition/1" or value.get("version") != version:
        raise ValueError("Selected source receipt does not match Core VERSION")
    if any(not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value[key]) for key in ("commit", "tree")):
        raise ValueError("Selected source receipt has invalid commit/tree identities")
    if any(not isinstance(value.get(key), str) or not value[key] for key in ("origin", "ref")):
        raise ValueError("Selected source receipt has no origin/ref")
    cli_root = source_root / "cli"
    if not cli_root.is_dir():
        raise ValueError("Acquisition receipt requires a complete verified source or distribution")
    if str(cli_root) not in sys.path:
        sys.path.insert(0, str(cli_root))
    if (source_root / ".git").exists():
        from _acquisition import verify_acquired
        if verify_acquired(source_root) != value:
            raise ValueError("Selected source receipt changed during verification")
    else:
        from _distribution import verify_distribution
        verify_distribution(source_root)
    return value


def write_source(vault: Path, source: dict) -> tuple[Path, ...]:
    """Write through the existing atomic file transaction; upgrades also journal this path."""
    plan = FilePlan()
    target = vault / ".brain/local/install-source.json"
    plan.write_text(target, json.dumps(source, sort_keys=True) + "\n")
    plan.validate()
    changes = plan.changes()
    apply_file_changes(changes)
    return tuple(change.path for change in changes)
