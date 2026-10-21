"""Explicit vault-local automatic capture selection."""

from dataclasses import dataclass, field
from typing import ClassVar
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult
from ..relationship._support import entry


@dataclass(frozen=True, slots=True)
class HistoryConfigureRequest:
    COMMAND_ID: ClassVar[str] = "history.configure"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    selected: tuple[str, ...] = field(metadata={"description": "Selected automatic types: native-transition, relationship-change, definition-change, correction; empty disables automatic capture."})
    expected_revision: str = field(metadata={"description": "Effective policy revision returned by history.policy, including built-in defaults."})


def decode(payload):
    reject_unexpected(payload, {"selected", "expected_revision"})
    return HistoryConfigureRequest(tuple(payload["selected"]), payload["expected_revision"])


def catalogue_entry():
    return entry(HistoryConfigureRequest, "configure", maintainer=True, summary="Configure the vault's selected automatic history classes without enabling external-edit capture or pruning.")
