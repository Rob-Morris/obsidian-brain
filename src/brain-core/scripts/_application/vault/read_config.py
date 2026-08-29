"""Typed privacy-bounded ``vault.read-config`` command owner."""

from __future__ import annotations

from .._decoding import decode_empty
from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class VaultConfigPayload:
    brain_name: str
    default_profile: str
    profiles: tuple[str, ...]
    semantic_processing: bool
    semantic_retrieval: bool
    semantic_engine_installed: bool
    artefact_sync_exclusions: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VaultReadConfigRequest:
    COMMAND_ID: ClassVar[str] = "vault.read-config"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = VaultConfigPayload


def execute(context: InvocationContext, _request: VaultReadConfigRequest):
    import warnings

    import config

    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            merged = config.load_config(context.selected_brain.vault_root)
    except (OSError, ValueError) as exc:
        return command_error(VaultReadConfigRequest, ErrorCode.CONFLICT, str(exc), None)

    vault = merged.get("vault", {})
    defaults = merged.get("defaults", {})
    flags = defaults.get("flags", {})
    local_runtime = defaults.get("local_runtime", {})
    exclude = defaults.get("exclude", {})
    for name, value in (
        ("defaults.flags", flags),
        ("defaults.local_runtime", local_runtime),
        ("defaults.exclude", exclude),
    ):
        if not isinstance(value, dict):
            return command_error(
                VaultReadConfigRequest,
                ErrorCode.CONFLICT,
                f"Invalid config: {name} must be a mapping",
                None,
            )
    exclusions = exclude.get("artefact_sync", ())
    if not isinstance(exclusions, list) or any(
        not isinstance(item, str) for item in exclusions
    ):
        return command_error(
            VaultReadConfigRequest,
            ErrorCode.CONFLICT,
            "Invalid config: defaults.exclude.artefact_sync must be a string list",
            None,
        )
    return Ok(
        VaultReadConfigRequest.COMMAND_ID,
        VaultReadConfigRequest.COMMAND_VERSION,
        VaultConfigPayload(
            brain_name=str(vault.get("brain_name") or ""),
            default_profile=str(defaults.get("default_profile") or ""),
            profiles=tuple(sorted(vault.get("profiles", {}))),
            semantic_processing=bool(flags.get("semantic_processing")),
            semantic_retrieval=bool(flags.get("semantic_retrieval")),
            semantic_engine_installed=bool(
                local_runtime.get("semantic_engine_installed")
            ),
            artefact_sync_exclusions=tuple(str(item) for item in exclusions),
            warnings=tuple(str(item.message) for item in captured),
        ),
    )


def decode(payload: Mapping[str, object]) -> VaultReadConfigRequest:
    return decode_empty(payload, VaultReadConfigRequest)


def catalogue_entry():
    return _catalogue_entry(VaultReadConfigRequest, execute)
