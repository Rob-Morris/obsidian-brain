"""Typed ``runtime.repair-router`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._router_maintenance import (
    RouterMaintenancePayload,
    catalogue_entry as router_catalogue_entry,
    decode_empty,
    execute_router_maintenance,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class RuntimeRepairRouterRequest:
    COMMAND_ID: ClassVar[str] = "runtime.repair-router"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RouterMaintenancePayload


def execute(context: InvocationContext, request: RuntimeRepairRouterRequest):
    return execute_router_maintenance(context, request, force=False)


def decode(payload: Mapping[str, object]) -> RuntimeRepairRouterRequest:
    return decode_empty(payload, RuntimeRepairRouterRequest)


def catalogue_entry():
    return router_catalogue_entry(RuntimeRepairRouterRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RuntimeRepairRouterRequest, decode)
