"""Typed exact-condition ``trigger.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_required_string,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


class TriggerCategory(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    ONGOING = "ongoing"


@dataclass(frozen=True, slots=True)
class TriggerReadPayload:
    condition: str
    target: str
    category: TriggerCategory
    detail: str | None


@dataclass(frozen=True, slots=True)
class TriggerReadRequest:
    COMMAND_ID: ClassVar[str] = "trigger.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TriggerReadPayload

    condition: str

    def __post_init__(self) -> None:
        if not isinstance(self.condition, str) or not self.condition.strip():
            raise ValueError("trigger.read condition must be a non-empty string")


def execute(context: InvocationContext, request: TriggerReadRequest):
    from _portable.router_collections import read_trigger_exact_from_vault

    try:
        trigger = read_trigger_exact_from_vault(
            context.selected_brain.vault_root,
            request.condition,
        )
    except FileNotFoundError as exc:
        return command_error(TriggerReadRequest, ErrorCode.CONFLICT, str(exc), None)
    except ValueError as exc:
        return command_error(
            TriggerReadRequest,
            ErrorCode.CONFLICT,
            str(exc),
            "condition",
        )
    if "error" in trigger:
        return command_error(
            TriggerReadRequest,
            ErrorCode.NOT_FOUND,
            str(trigger["error"]),
            "condition",
        )
    return Ok(
        TriggerReadRequest.COMMAND_ID,
        TriggerReadRequest.COMMAND_VERSION,
        TriggerReadPayload(
            condition=trigger["condition"],
            target=trigger["target"],
            category=TriggerCategory(trigger["category"]),
            detail=trigger.get("detail"),
        ),
    )


def decode(payload: Mapping[str, object]) -> TriggerReadRequest:
    return decode_required_string(payload, "condition", TriggerReadRequest)


def catalogue_entry():
    return _catalogue_entry(TriggerReadRequest, execute)


def resolver_entry():
    return _resolver_entry(TriggerReadRequest, decode)
