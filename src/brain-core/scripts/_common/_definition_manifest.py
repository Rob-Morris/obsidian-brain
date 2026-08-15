"""Pure decoding for artefact-definition manifests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._filesystem import validate_portable_relative_path


def decode_definition_manifest(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise one parsed ``manifest.yaml`` mapping."""
    files = data.get("files")
    if not isinstance(files, Mapping) or not files:
        raise ValueError("files must be a non-empty mapping")

    decoded_files: dict[str, dict[str, str]] = {}
    for role, metadata in files.items():
        if not isinstance(role, str) or not isinstance(metadata, Mapping):
            raise ValueError("file roles must map string names to metadata mappings")
        source = metadata.get("source")
        target = metadata.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError(f"role {role!r} needs string source and target")
        for label, path in (("source", source), ("target", target)):
            try:
                validate_portable_relative_path(path)
            except ValueError as exc:
                raise ValueError(
                    f"role {role!r} has invalid {label} {path!r}: {exc}"
                ) from exc
        decoded_files[role] = {"source": source, "target": target}

    folders = data.get("folders", [])
    if not isinstance(folders, list) or not all(
        isinstance(folder, str) for folder in folders
    ):
        raise ValueError("folders must be a list of paths")
    for folder in folders:
        try:
            validate_portable_relative_path(folder, allow_trailing_slash=True)
        except ValueError as exc:
            raise ValueError(f"invalid folder {folder!r}: {exc}") from exc

    decoded: dict[str, Any] = {
        "files": decoded_files,
        "folders": list(folders),
    }
    router_trigger = data.get("router_trigger")
    if router_trigger is not None:
        if not isinstance(router_trigger, str):
            raise ValueError("router_trigger must be a string")
        decoded["router_trigger"] = router_trigger
    return decoded
