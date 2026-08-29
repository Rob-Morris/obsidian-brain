"""Typed ``runtime.refresh-router`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._router_maintenance import (
    RouterMaintenancePayload,
    catalogue_entry as router_catalogue_entry,
    execute_router_maintenance,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class RuntimeRefreshRouterRequest:
    COMMAND_ID: ClassVar[str] = "runtime.refresh-router"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RouterMaintenancePayload

    force: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.force, bool):
            raise ValueError("runtime.refresh-router force must be a boolean")


def execute(context: InvocationContext, request: RuntimeRefreshRouterRequest):
    return execute_router_maintenance(context, request, force=request.force)


def decode(payload: Mapping[str, object]) -> RuntimeRefreshRouterRequest:
    reject_unexpected(payload, {"force"})
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("force must be a boolean")
    return RuntimeRefreshRouterRequest(force)


def catalogue_entry():
    return router_catalogue_entry(RuntimeRefreshRouterRequest, execute)
