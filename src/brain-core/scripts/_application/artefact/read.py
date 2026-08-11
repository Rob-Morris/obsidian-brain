"""Typed ``artefact.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import CommandError, Error, ErrorCode, Ok, RequestErrorDetails
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


@dataclass(frozen=True, slots=True)
class ArtefactReadPayload:
    reference: str
    location: "ArtefactLocation"
    content: str


class ArtefactLocation(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ArtefactReadRequest:
    COMMAND_ID: ClassVar[str] = "artefact.read"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactReadPayload

    reference: str
    location: ArtefactLocation = ArtefactLocation.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("artefact.read reference must be a non-empty string")
        if not isinstance(self.location, ArtefactLocation):
            raise ValueError("artefact.read location must use ArtefactLocation")


def execute(context: InvocationContext, request: ArtefactReadRequest):
    from _common import MissingFileResult
    from _portable.artefact_read import read_from_vault
    from _portable.vault_files import read_archived_artefact

    if request.location is ArtefactLocation.ARCHIVED:
        result = read_archived_artefact(
            context.selected_brain.vault_root,
            request.reference,
        )
    else:
        result = read_from_vault(
            context.selected_brain.vault_root,
            request.reference,
        )
    if isinstance(result, MissingFileResult):
        return _error(ErrorCode.NOT_FOUND, result.message)
    if isinstance(result, dict) and "error" in result:
        message = str(result["error"])
        if request.location is ArtefactLocation.ARCHIVED:
            return _error(ErrorCode.INVALID_REQUEST, message)
        if "escapes vault root" in message:
            return _error(ErrorCode.INVALID_REQUEST, message)
        if "router" in message.casefold():
            return _error(ErrorCode.CONFLICT, message)
        return _error(ErrorCode.NOT_FOUND, message)
    if not isinstance(result, str):
        raise TypeError("portable artefact reader returned a non-text result")
    return Ok(
        ArtefactReadRequest.COMMAND_ID,
        ArtefactReadRequest.COMMAND_VERSION,
        ArtefactReadPayload(request.reference, request.location, result),
    )


def _error(code: ErrorCode, message: str) -> Error:
    return Error(
        ArtefactReadRequest.COMMAND_ID,
        ArtefactReadRequest.COMMAND_VERSION,
        CommandError(
            code,
            message,
            RequestErrorDetails("reference", message),
        ),
    )


def decode(payload: Mapping[str, object]) -> ArtefactReadRequest:
    unexpected = sorted(set(payload) - {"reference", "location"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    location = payload.get("location", ArtefactLocation.ACTIVE.value)
    if not isinstance(location, str):
        raise ValueError("location must be a string")
    try:
        return ArtefactReadRequest(reference, ArtefactLocation(location))
    except ValueError as exc:
        if location not in {item.value for item in ArtefactLocation}:
            raise ValueError("location must be active or archived") from exc
        raise


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=ArtefactReadRequest,
        executor=execute,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactReadRequest, decode)
