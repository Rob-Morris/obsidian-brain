"""Typed exact-key ``type.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


class ArtefactTypeClassification(str, Enum):
    LIVING = "living"
    TEMPORAL = "temporal"


@dataclass(frozen=True, slots=True)
class ArtefactTypeReadPayload:
    key: str
    classification: ArtefactTypeClassification
    frontmatter_type: str
    folder: str
    path: str
    configured: bool
    taxonomy_path: str | None
    template_path: str | None
    definition: str | None


@dataclass(frozen=True, slots=True)
class ArtefactTypeReadRequest:
    COMMAND_ID: ClassVar[str] = "type.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactTypeReadPayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("type.read reference must be a non-empty string")


def execute(context: InvocationContext, request: ArtefactTypeReadRequest):
    from _common import MissingFileResult
    from _portable.type_definitions import read_type_exact_from_vault

    try:
        result = read_type_exact_from_vault(
            context.selected_brain.vault_root,
            request.reference,
        )
    except (FileNotFoundError, ValueError) as exc:
        return command_error(ArtefactTypeReadRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(result, dict):
        return command_error(
            ArtefactTypeReadRequest,
            ErrorCode.NOT_FOUND,
            str(result["error"]),
            "reference",
        )
    metadata, definition = result
    if isinstance(definition, MissingFileResult):
        return command_error(
            ArtefactTypeReadRequest,
            ErrorCode.CONFLICT,
            definition.message,
            None,
        )
    return Ok(
        ArtefactTypeReadRequest.COMMAND_ID,
        ArtefactTypeReadRequest.COMMAND_VERSION,
        ArtefactTypeReadPayload(
            key=metadata["key"],
            classification=ArtefactTypeClassification(metadata["classification"]),
            frontmatter_type=metadata["frontmatter_type"],
            folder=metadata["folder"],
            path=metadata["path"],
            configured=bool(metadata["configured"]),
            taxonomy_path=metadata.get("taxonomy_file"),
            template_path=metadata.get("template_file"),
            definition=definition,
        ),
    )


def decode(payload: Mapping[str, object]) -> ArtefactTypeReadRequest:
    return decode_reference(payload, ArtefactTypeReadRequest)


def catalogue_entry():
    return _catalogue_entry(ArtefactTypeReadRequest, execute)


def resolver_entry():
    return _resolver_entry(ArtefactTypeReadRequest, decode)
