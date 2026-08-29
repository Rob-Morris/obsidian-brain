"""Typed ``runtime.read-environment`` command and internal executor."""

from __future__ import annotations

from .._decoding import decode_empty
from .._read_support import catalogue_entry as portable_reader_entry
from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import Ok


RuntimeValue = str | bool | int | float | None


@dataclass(frozen=True, slots=True)
class RuntimeFact:
    name: str
    value: RuntimeValue


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentPayload:
    facts: tuple[RuntimeFact, ...]


@dataclass(frozen=True, slots=True)
class RuntimeReadEnvironmentRequest:
    COMMAND_ID: ClassVar[str] = "runtime.read-environment"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeEnvironmentPayload


def execute(context: InvocationContext, _request: RuntimeReadEnvironmentRequest):
    from _portable.router_views import environment_from_vault

    environment = environment_from_vault(context.selected_brain.vault_root)
    facts = tuple(
        RuntimeFact(name, value)
        for name, value in sorted(environment.items())
        if isinstance(value, (str, bool, int, float)) or value is None
    )
    if len(facts) != len(environment):
        raise TypeError("runtime environment contained a non-scalar value")
    return Ok(
        RuntimeReadEnvironmentRequest.COMMAND_ID,
        RuntimeReadEnvironmentRequest.COMMAND_VERSION,
        RuntimeEnvironmentPayload(facts),
    )


def decode(payload: Mapping[str, object]) -> RuntimeReadEnvironmentRequest:
    return decode_empty(payload, RuntimeReadEnvironmentRequest)


def catalogue_entry():
    return portable_reader_entry(RuntimeReadEnvironmentRequest, execute)
