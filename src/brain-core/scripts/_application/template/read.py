"""Typed exact-type-key ``template.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class TemplateReadPayload:
    type_key: str
    artefact_type: str
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class TemplateReadRequest:
    COMMAND_ID: ClassVar[str] = "template.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TemplateReadPayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("template.read reference must be a non-empty string")


def execute(context: InvocationContext, request: TemplateReadRequest):
    from _common import MissingFileResult
    from _portable.type_definitions import read_template_exact_from_vault

    try:
        result = read_template_exact_from_vault(
            context.selected_brain.vault_root,
            request.reference,
        )
    except (FileNotFoundError, ValueError) as exc:
        return command_error(TemplateReadRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(result, dict):
        return command_error(
            TemplateReadRequest,
            ErrorCode.NOT_FOUND,
            str(result["error"]),
            "reference",
        )
    metadata, content = result
    if isinstance(content, MissingFileResult):
        return command_error(
            TemplateReadRequest,
            ErrorCode.CONFLICT,
            content.message,
            None,
        )
    return Ok(
        TemplateReadRequest.COMMAND_ID,
        TemplateReadRequest.COMMAND_VERSION,
        TemplateReadPayload(
            type_key=metadata["key"],
            artefact_type=metadata["frontmatter_type"],
            path=metadata["template_file"],
            content=content,
        ),
    )


def decode(payload: Mapping[str, object]) -> TemplateReadRequest:
    return decode_reference(payload, TemplateReadRequest)


def catalogue_entry():
    return _catalogue_entry(TemplateReadRequest, execute)


def resolver_entry():
    return _resolver_entry(TemplateReadRequest, decode)
