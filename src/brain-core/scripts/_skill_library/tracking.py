"""Persistence for user sources, core-derived overrides, and source checks."""

from __future__ import annotations

import json
from pathlib import Path
import re

from _common import safe_write_json, validate_portable_relative_path

from .packages import validate_skill_name


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
TRACKING_REL = Path(".brain") / "skill-sources.json"
CORE_MANIFEST_REL = Path(".brain-core") / "skill-sources.json"


class TrackingError(ValueError):
    """Skill tracking metadata is unreadable or structurally invalid."""


def empty_tracking() -> dict[str, object]:
    """Return a new empty tracking document at the current schema version."""
    return {
        "schema_version": SCHEMA_VERSION,
        "managed": {},
        "core_overrides": {},
        "core_checks": {},
    }


def load_tracking(vault_root: str | Path) -> dict[str, object]:
    """Load and validate mutable user-skill lifecycle metadata."""
    path = Path(vault_root) / TRACKING_REL
    if path.is_symlink():
        raise TrackingError(f"skill tracking path is a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_tracking()
    except (OSError, json.JSONDecodeError) as exc:
        raise TrackingError(f"cannot read skill tracking metadata: {exc}") from exc
    _validate_tracking(value)
    value.setdefault("core_overrides", {})
    return value


def write_tracking(vault_root: str | Path, value: dict[str, object]) -> None:
    """Validate and atomically persist mutable user-skill lifecycle metadata."""
    _validate_tracking(value)
    root = Path(vault_root)
    brain_dir = root / ".brain"
    if brain_dir.is_symlink():
        raise TrackingError(f"Brain operational directory is a symlink: {brain_dir}")
    path = root / TRACKING_REL
    safe_write_json(path, value, bounds=root, follow_symlinks=False)


def load_core_manifest(vault_root: str | Path) -> dict[str, dict[str, object]]:
    """Load immutable external-source descriptors shipped by Brain core."""
    path = Path(vault_root) / CORE_MANIFEST_REL
    if path.is_symlink():
        raise TrackingError(f"core skill-source manifest is a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise TrackingError(f"cannot read core skill-source manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise TrackingError("core skill-source manifest has an unsupported schema")
    skills = value.get("skills")
    if not isinstance(skills, dict):
        raise TrackingError("core skill-source manifest skills must be an object")
    result: dict[str, dict[str, object]] = {}
    for name, record in skills.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise TrackingError("core skill-source manifest entries are invalid")
        _validate_name(name, label="core skill-source manifest")
        _validate_source(record, label=f"core skill {name}")
        result[name] = dict(record)
    return result


def _validate_tracking(value: object) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise TrackingError("skill tracking metadata has an unsupported schema")
    managed = value.get("managed")
    overrides = value.get("core_overrides", {})
    checks = value.get("core_checks")
    if (
        not isinstance(managed, dict)
        or not isinstance(overrides, dict)
        or not isinstance(checks, dict)
    ):
        raise TrackingError("skill tracking collections must be objects")
    for name, record in managed.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise TrackingError("managed skill tracking entry is invalid")
        _validate_name(name, label="managed skill tracking")
        _validate_source(record, label=f"managed skill {name}")
        required = {
            "resolved_commit",
            "source_package_sha256",
            "installed_baseline_sha256",
            "installed_manifest",
            "installed_at",
            "last_checked_at",
        }
        if any(key not in record for key in required):
            raise TrackingError(f"managed skill {name} is missing required fields")
        _validate_manifest(record["installed_manifest"], label=f"managed skill {name}")
    for name, record in overrides.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise TrackingError("core-derived user override entry is invalid")
        _validate_name(name, label="core-derived user override")
        required = {
            "core_lineage",
            "installed_baseline_sha256",
            "installed_manifest",
            "materialised_at",
        }
        if any(key not in record for key in required):
            raise TrackingError(
                f"core-derived user override {name} is missing required fields"
            )
        if record["core_lineage"] != name:
            raise TrackingError(
                f"core-derived user override {name} has mismatched lineage"
            )
        _validate_manifest(
            record["installed_manifest"],
            label=f"core-derived user override {name}",
        )
    for name, record in checks.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise TrackingError("core source-check entry is invalid")
        _validate_name(name, label="core source-check entry")


def _validate_source(record: dict[str, object], *, label: str) -> None:
    for key in ("repository", "skill_path", "configured_ref"):
        if not isinstance(record.get(key), str) or not str(record[key]).strip():
            raise TrackingError(f"{label} requires a non-empty {key}")


def _validate_name(name: str, *, label: str) -> None:
    try:
        validate_skill_name(name)
    except ValueError as exc:
        raise TrackingError(f"{label} has an invalid skill name: {name!r}") from exc


def _validate_manifest(value: object, *, label: str) -> None:
    if not isinstance(value, list):
        raise TrackingError(f"{label} manifest must be a list")
    folded_paths: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            raise TrackingError(f"{label} manifest entries must be objects")
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        executable = item.get("executable")
        if not isinstance(path, str):
            raise TrackingError(f"{label} manifest path must be a string")
        try:
            validate_portable_relative_path(path)
        except ValueError as exc:
            raise TrackingError(f"{label} manifest has an unsafe path: {path!r}") from exc
        previous = folded_paths.get(path.casefold())
        if previous is not None:
            raise TrackingError(
                f"{label} manifest repeats a portable path: {previous!r}, {path!r}"
            )
        folded_paths[path.casefold()] = path
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise TrackingError(f"{label} manifest has an invalid SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise TrackingError(f"{label} manifest has an invalid size")
        if not isinstance(executable, bool):
            raise TrackingError(f"{label} manifest has an invalid executable flag")
