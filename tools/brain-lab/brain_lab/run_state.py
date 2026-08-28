from __future__ import annotations

from pathlib import Path
from typing import Any

from .application import OperationContext
from .docker import DockerError
from .manifests import read_gzip_json


def capture_run_manifest(
    context: OperationContext,
    container: str,
    evidence_name: str,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    try:
        execution = context.docker.exec(
            container,
            [
                "python3.12",
                "/usr/local/lib/brain-lab/tree_manifest.py",
                "--root",
                "/home/brain",
                "--scope",
                "run",
                "--gzip",
            ],
            evidence_directory=context.evidence_directory / evidence_name,
            timeout_seconds=600,
        )
    except DockerError as exc:
        return None, f"{type(exc).__name__}: {exc}", False
    if not execution.succeeded or execution.stdout.truncated:
        return None, "manifest command failed or exceeded its retention bound", False
    try:
        value = read_gzip_json(Path(execution.stdout.path))
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}", False
    if not isinstance(value.get("entries"), list):
        return None, "manifest command returned an invalid shape", False
    return value, None, execution.evidence_complete


def filesystem_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_entries = {entry["path"]: entry for entry in before["entries"]}
    after_entries = {entry["path"]: entry for entry in after["entries"]}
    added = [after_entries[path] for path in sorted(after_entries.keys() - before_entries)]
    removed = [before_entries[path] for path in sorted(before_entries.keys() - after_entries)]
    changed = [
        {"path": path, "before": before_entries[path], "after": after_entries[path]}
        for path in sorted(before_entries.keys() & after_entries)
        if before_entries[path] != after_entries[path]
    ]
    return {
        "schema": "brain-lab.filesystem-diff/1",
        "before_tree_sha256": before["tree_sha256"],
        "after_tree_sha256": after["tree_sha256"],
        "equal": before["tree_sha256"] == after["tree_sha256"],
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }
