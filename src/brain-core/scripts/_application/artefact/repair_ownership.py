"""Typed ``artefact.repair-ownership`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._repair_owners import (
    ArtefactRepairPayload,
    ArtefactRepairRequest,
    catalogue_entry as repair_catalogue_entry,
    decode_empty,
    execute_repair,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactRepairOwnershipRequest(ArtefactRepairRequest):
    COMMAND_ID: ClassVar[str] = "artefact.repair-ownership"
    RESULT_TYPE: ClassVar[type] = ArtefactRepairPayload


def execute(context: InvocationContext, request: ArtefactRepairOwnershipRequest):
    import _repair_runtime

    return execute_repair(
        context,
        request,
        operation=lambda root, dry_run: _repair_runtime.repair_ownership_locked(
            root, dry_run
        ),
    )


def decode(payload: Mapping[str, object]) -> ArtefactRepairOwnershipRequest:
    return decode_empty(payload, ArtefactRepairOwnershipRequest)


def catalogue_entry():
    return repair_catalogue_entry(ArtefactRepairOwnershipRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactRepairOwnershipRequest, decode)
