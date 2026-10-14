"""Retain one immutable original manifest for the first workspace schema cutover."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path


RECOVERY_REL = Path(".brain/local/workspace.pre-schema-1.json")


def stage_legacy_workspace_recovery(plan, state) -> Path | None:
    """Stage exact legacy bytes, refusing to overwrite a different original.

    One fixed file per folder bounds history for this schema transition. It is
    recovery evidence only; manifest and registry remain the binding authorities.
    """
    if state.data is None or "schema_version" in state.data:
        return None
    source = state.source_path
    original = plan.read_bytes(source)
    if original is None:
        raise ValueError(f"Legacy workspace manifest disappeared: {source}")
    path = state.target_dir / RECOVERY_REL
    payload = {"schema": "brain.workspace-schema-recovery/1", "source": str(source),
               "sha256": hashlib.sha256(original).hexdigest(),
               "before_base64": base64.b64encode(original).decode("ascii")}
    existing = plan.read_text(path)
    if existing is not None:
        try:
            observed = json.loads(existing)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Workspace recovery file is invalid: {path}; inspect and move it explicitly before retrying") from exc
        if observed != payload:
            raise ValueError(f"Workspace recovery file already retains a different original: {path}; "
                             "inspect and preserve or move it explicitly before retrying")
        return path
    plan.write_text(path, json.dumps(payload, sort_keys=True) + "\n")
    return path
