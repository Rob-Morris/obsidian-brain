"""Typed ``artefact.outline`` command and internal executor."""

from __future__ import annotations

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as portable_reader_entry

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok, request_error


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


def read_result(context: InvocationContext, request: ArtefactOutlineRequest):
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
    from pathlib import Path
    from ..preparation import ObservedRead, ObservedResource

    payload = Ok(
        ArtefactOutlineRequest.COMMAND_ID,
        ArtefactOutlineRequest.COMMAND_VERSION,
        ArtefactOutlinePayload(result["path"], targets),
    )

    relative = Path(result["source_path"]).relative_to(context.selected_brain.vault_root.resolve()).as_posix()
    return ObservedRead(payload, (ObservedResource("document", relative, result["revision"]),))


def execute(context, request):
    from ..preparation import execute_prepared_read

    return execute_prepared_read(context, request, read_result)


def _error(code: ErrorCode, message: str) -> Error:
    return request_error(ArtefactOutlineRequest, code, message, "reference")


def decode(payload: Mapping[str, object]) -> ArtefactOutlineRequest:
    reject_unexpected(payload, {"reference"})
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    return ArtefactOutlineRequest(reference)


def _reader_entry():
    return portable_reader_entry(ArtefactOutlineRequest, execute)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import ResultReadPreparation

    return replace(_reader_entry(), preparation=ResultReadPreparation(read_result))
