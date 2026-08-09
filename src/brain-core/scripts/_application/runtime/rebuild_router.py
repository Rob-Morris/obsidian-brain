"""Typed ``runtime.rebuild-router`` owner."""

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
class RuntimeRebuildRouterRequest:
    COMMAND_ID: ClassVar[str] = "runtime.rebuild-router"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RouterMaintenancePayload


def execute(context: InvocationContext, request: RuntimeRebuildRouterRequest):
    return execute_router_maintenance(context, request, force=True)


def decode(payload: Mapping[str, object]) -> RuntimeRebuildRouterRequest:
    return decode_empty(payload, RuntimeRebuildRouterRequest)


def catalogue_entry():
    return router_catalogue_entry(RuntimeRebuildRouterRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RuntimeRebuildRouterRequest, decode)
