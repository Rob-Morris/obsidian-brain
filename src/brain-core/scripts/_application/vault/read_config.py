"""Typed privacy-bounded ``vault.read-config`` command owner."""

from __future__ import annotations

from .._decoding import reject_unexpected
from .._response_budget import (ContentRange, TextCursor, bounded_text_result, decode_text_cursor,
    encoded_result_size, validate_text_window, MODEL_TEXT_BUDGET, DEFAULT_TEXT_CHARACTERS, TEXT_WINDOW_DESCRIPTIONS)
from ..access_contracts import AccessConfiguration
from dataclasses import dataclass, asdict
import json
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
    access: AccessConfiguration


@dataclass(frozen=True, slots=True)
class VaultConfigPage:
    content: str
    revision: str
    range: ContentRange
    instruction: str


@dataclass(frozen=True, slots=True)
class VaultReadConfigRequest:
    COMMAND_ID: ClassVar[str] = "vault.read-config"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[object] = VaultConfigPayload | VaultConfigPage
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = TEXT_WINDOW_DESCRIPTIONS

    cursor: TextCursor | None = None
    max_characters: int = DEFAULT_TEXT_CHARACTERS

    def __post_init__(self):
        validate_text_window(self.cursor, self.max_characters)


def execute(context: InvocationContext, request: VaultReadConfigRequest):
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
    payload = VaultConfigPayload(
        brain_name=str(vault.get("brain_name") or ""),
        default_profile=str(defaults.get("default_profile") or ""),
        profiles=tuple(sorted(vault.get("profiles", {}))),
        semantic_processing=bool(flags.get("semantic_processing")),
        semantic_retrieval=bool(flags.get("semantic_retrieval")),
        semantic_engine_installed=bool(local_runtime.get("semantic_engine_installed")),
        artefact_sync_exclusions=tuple(str(item) for item in exclusions),
        warnings=tuple(str(item.message) for item in captured),
        access=context.access.configuration(),
    )
    complete = Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)
    if request.cursor is None and encoded_result_size(complete) < MODEL_TEXT_BUDGET:
        return complete
    from ..preparation import content_digest
    content = json.dumps(asdict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision = content_digest(content)
    return bounded_text_result(VaultReadConfigRequest, content, revision,
        cursor=request.cursor, max_characters=request.max_characters, byte_budget=MODEL_TEXT_BUDGET - 1,
        payload=lambda text, window: VaultConfigPage(text, revision, window,
            "Concatenate content from all range.next_cursor pages before parsing the redacted configuration JSON."))


def decode(payload: Mapping[str, object]) -> VaultReadConfigRequest:
    reject_unexpected(payload, {"cursor", "max_characters"})
    return VaultReadConfigRequest(decode_text_cursor(payload.get("cursor")),
                                  payload.get("max_characters", DEFAULT_TEXT_CHARACTERS))


def catalogue_entry():
    return _catalogue_entry(VaultReadConfigRequest, execute)
