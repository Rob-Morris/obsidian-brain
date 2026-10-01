"""Typed ``maintenance.list`` owner: what needs a person, with claim state (§7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from _bootstrap.maintenance_decisions import ItemState
from _bootstrap.maintenance_findings import Disposition, Owner
from _bootstrap.maintenance_summary import SHOWN_AUTOMATIC, LastPassSummary

from .._decoding import decode_bool, reject_unexpected
from .._read_support import catalogue_entry as _read_entry
from ..context import InvocationContext
from ..results import Ok
from ..types import Projection
from ._items import MaintenanceItem, describe, last_outcome_for, load_state


@dataclass(frozen=True, slots=True)
class MaintenanceListPayload:
    items: tuple[MaintenanceItem, ...]
    hidden_quiet: int
    last_pass: LastPassSummary | None


@dataclass(frozen=True, slots=True)
class MaintenanceListRequest:
    COMMAND_ID: ClassVar[str] = "maintenance.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MaintenanceListPayload

    all: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.all, bool):
            raise ValueError("maintenance.list all must be a boolean")


def execute(context: InvocationContext, request: MaintenanceListRequest):
    state, error = load_state(MaintenanceListRequest, context)
    if error is not None:
        return error
    root = context.selected_brain.vault_root
    items = []
    hidden = 0
    for group in state.groups:
        decision = state.states[group.key]
        item = describe(group, decision, vault_root=root, last_outcome=last_outcome_for(group, state.last_pass))
        if group.owner is Owner.MACHINE:
            items.append(item)
        elif group.disposition is Disposition.AUTOMATIC:
            if request.all or decision.state is ItemState.CLAIM_EXPIRED or item.last_outcome in SHOWN_AUTOMATIC:
                items.append(item)
        elif decision.state is ItemState.QUIET and not request.all:
            hidden += 1
        else:
            items.append(item)
    last_pass = None if state.last_pass is None else LastPassSummary.from_document(state.last_pass)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, MaintenanceListPayload(tuple(items), hidden, last_pass))


def decode(payload: Mapping[str, object]) -> MaintenanceListRequest:
    reject_unexpected(payload, {"all"})
    return MaintenanceListRequest(decode_bool(payload, "all"))


def catalogue_entry():
    from dataclasses import replace

    from ..catalogue import exclude_projection

    entry = replace(_read_entry(MaintenanceListRequest, execute),
                    summary="List maintenance findings that need a person, with their claim state.")
    return exclude_projection(entry, Projection.MCP, "maintenance administration is CLI and direct-script only")
