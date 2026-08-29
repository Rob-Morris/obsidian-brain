from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .application import OperationContext
from .container_contract import CONTAINER_PYTHON
from .docker import DockerError
from .manifests import read_gzip_json
from .process import ProcessExecution


@dataclass(frozen=True)
class RunManifestCapture:
    manifest: dict[str, Any]
    evidence_complete: bool


@dataclass(frozen=True)
class RunManifestHelper:
    content: bytes
    sha256: str


class RunManifestCaptureError(RuntimeError):
    def __init__(self, message: str, execution: ProcessExecution | None = None):
        super().__init__(message)
        self.execution = execution

    @property
    def evidence_complete(self) -> bool:
        return self.execution is not None and self.execution.evidence_complete


def load_run_manifest_helper(context: OperationContext) -> RunManifestHelper:
    try:
        content = (context.tool_root / "container" / "tree_manifest.py").read_bytes()
    except OSError as exc:
        raise RunManifestCaptureError(
            f"Brain Lab run-manifest helper is unavailable: {exc}"
        ) from exc
    return RunManifestHelper(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def capture_run_manifest(
    context: OperationContext,
    container: str,
    evidence_name: str,
    helper: RunManifestHelper,
) -> RunManifestCapture:
    evidence_directory = context.evidence_directory / evidence_name
    try:
        evidence_directory.mkdir(parents=True, exist_ok=True)
        (evidence_directory / "manifest-helper.json").write_text(
            json.dumps(
                {
                    "schema": "brain-lab.run-manifest-helper/1",
                    "sha256": helper.sha256,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RunManifestCaptureError(
            f"Brain Lab run-manifest helper evidence could not be retained: {exc}"
        ) from exc
    try:
        execution = context.docker.exec(
            container,
            [
                CONTAINER_PYTHON,
                "-",
                "--root",
                "/home/brain",
                "--scope",
                "run",
                "--gzip",
            ],
            environment={"PYTHONDONTWRITEBYTECODE": "1"},
            stdin=helper.content,
            evidence_directory=evidence_directory,
            timeout_seconds=600,
        )
    except DockerError as exc:
        raise RunManifestCaptureError(
            f"{type(exc).__name__}: {exc}", exc.execution
        ) from exc
    if not execution.succeeded or execution.stdout.truncated:
        raise RunManifestCaptureError(
            "manifest command failed or exceeded its retention bound", execution
        )
    try:
        value = read_gzip_json(Path(execution.stdout.path))
    except (OSError, ValueError) as exc:
        raise RunManifestCaptureError(f"{type(exc).__name__}: {exc}", execution) from exc
    if not isinstance(value.get("entries"), list):
        raise RunManifestCaptureError("manifest command returned an invalid shape", execution)
    return RunManifestCapture(value, execution.evidence_complete)


def filesystem_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if before["tree_sha256"] == after["tree_sha256"]:
        return {
            "schema": "brain-lab.filesystem-diff/1",
            "before_tree_sha256": before["tree_sha256"],
            "after_tree_sha256": after["tree_sha256"],
            "equal": True,
            "summary": {"added": 0, "removed": 0, "changed": 0},
            "added": [],
            "removed": [],
            "changed": [],
        }
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
