"""Typed ``artefact.outline`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
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
class StructuralTarget:
    kind: str
    target: str
    level: int | None
    line: int
    occurrence: int
    within: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtefactOutlinePayload:
    reference: str
    targets: tuple[StructuralTarget, ...]


@dataclass(frozen=True, slots=True)
class ArtefactOutlineRequest:
    COMMAND_ID: ClassVar[str] = "artefact.outline"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactOutlinePayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("artefact.outline reference must be a non-empty string")


def execute(context: InvocationContext, request: ArtefactOutlineRequest):
    from _portable.artefact_outline import outline_from_vault

    try:
        result = outline_from_vault(
            context.selected_brain.vault_root,
            request.reference,
        )
    except FileNotFoundError as exc:
        return _error(ErrorCode.NOT_FOUND, str(exc))
    except ValueError as exc:
        message = str(exc)
        if "escapes vault root" in message:
            return _error(ErrorCode.INVALID_REQUEST, message)
        if "No artefact" in message or "not found" in message.casefold():
            return _error(ErrorCode.NOT_FOUND, message)
        return _error(ErrorCode.CONFLICT, message)
    targets = tuple(
        StructuralTarget(
            kind=item["kind"],
            target=item["target"],
            level=item["level"],
            line=item["line"],
            occurrence=item["occurrence"],
            within=tuple(item["within"]),
        )
        for item in result["targets"]
    )
    return Ok(
        ArtefactOutlineRequest.COMMAND_ID,
        ArtefactOutlineRequest.COMMAND_VERSION,
        ArtefactOutlinePayload(result["path"], targets),
    )


def _error(code: ErrorCode, message: str) -> Error:
    return Error(
        ArtefactOutlineRequest.COMMAND_ID,
        ArtefactOutlineRequest.COMMAND_VERSION,
        CommandError(
            code,
            message,
            RequestErrorDetails("reference", message),
        ),
    )


def decode(payload: Mapping[str, object]) -> ArtefactOutlineRequest:
    unexpected = sorted(set(payload) - {"reference"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    return ArtefactOutlineRequest(reference)


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=ArtefactOutlineRequest,
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

    return ResolverEntry(ArtefactOutlineRequest, decode)
