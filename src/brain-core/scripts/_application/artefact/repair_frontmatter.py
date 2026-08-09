"""Typed ``artefact.repair-frontmatter`` owner."""

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
class ArtefactRepairFrontmatterRequest(ArtefactRepairRequest):
    COMMAND_ID: ClassVar[str] = "artefact.repair-frontmatter"
    RESULT_TYPE: ClassVar[type] = ArtefactRepairPayload


def execute(context: InvocationContext, request: ArtefactRepairFrontmatterRequest):
    import _repair_runtime

    return execute_repair(
        context,
        request,
        operation=lambda root, dry_run: _repair_runtime.repair_frontmatter(
            root, dry_run
        ),
    )


def decode(payload: Mapping[str, object]) -> ArtefactRepairFrontmatterRequest:
    return decode_empty(payload, ArtefactRepairFrontmatterRequest)


def catalogue_entry():
    return repair_catalogue_entry(ArtefactRepairFrontmatterRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactRepairFrontmatterRequest, decode)
