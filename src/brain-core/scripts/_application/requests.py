"""Sealed typed requests for foundational application commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .artefact.list import ArtefactListRequest
from .artefact.list_archived import ArtefactListArchivedRequest
from .artefact.outline import ArtefactOutlineRequest
from .artefact.read import ArtefactReadRequest
from .artefact.read_archived import ArtefactReadArchivedRequest
from .links.check import LinksCheckRequest
from .memory.list import MemoryListRequest
from .memory.read import MemoryReadRequest
from .plugin.list import PluginListRequest
from .plugin.read import PluginReadRequest
from .runtime.read_environment import RuntimeReadEnvironmentRequest
from .session.start import SessionStartRequest
from .skill.list import SkillListRequest
from .skill.read import SkillReadRequest
from .style.list import StyleListRequest
from .style.read import StyleReadRequest
from .template.list import TemplateListRequest
from .template.read import TemplateReadRequest
from .trigger.list import TriggerListRequest
from .trigger.read import TriggerReadRequest
from .type.list import ArtefactTypeListRequest
from .type.read import ArtefactTypeReadRequest
from .type.status import TypeStatusRequest
from .vault.check import VaultCheckRequest
from .vault.read_config import VaultReadConfigRequest
from .vault.read_router import VaultReadRouterRequest
from .vault.read_file import VaultReadFileRequest
from .workspace.list import WorkspaceListRequest
from .workspace.read import WorkspaceReadRequest
from .workspace.resolve import WorkspaceResolveRequest
from .receipts import OutcomeReceipt, OutcomeReference, ReceiptLookupState
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    validate_command_id,
)


@dataclass(frozen=True, slots=True)
class CommandListPayload:
    command_ids: tuple[str, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        for command_id in self.command_ids:
            validate_command_id(command_id)
        if tuple(sorted(self.command_ids)) != self.command_ids:
            raise ValueError("command list payload identifiers must be sorted")
        if len(self.command_ids) != len(set(self.command_ids)):
            raise ValueError("command list payload identifiers must be unique")
        if self.next_cursor is not None:
            validate_command_id(self.next_cursor)
            if not self.command_ids or self.next_cursor != self.command_ids[-1]:
                raise ValueError("command list next_cursor must identify the final page item")


@dataclass(frozen=True, slots=True)
class CommandDescriptionPayload:
    command_id: str
    command_version: int

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1:
            raise ValueError("command description version must be positive")


@dataclass(frozen=True, slots=True)
class InvocationReadPayload:
    reference: OutcomeReference
    state: ReceiptLookupState
    receipt: OutcomeReceipt | None = None

    def __post_init__(self) -> None:
        if self.state is ReceiptLookupState.FOUND and self.receipt is None:
            raise ValueError("found invocation outcome requires a receipt")
        if self.state is ReceiptLookupState.STILL_UNKNOWN and self.receipt is not None:
            raise ValueError("still-unknown invocation outcome cannot carry a receipt")
        if self.receipt is not None and self.receipt.reference != self.reference:
            raise ValueError("invocation outcome reference must match its receipt")


@dataclass(frozen=True, slots=True)
class CommandListRequest:
    COMMAND_ID: ClassVar[str] = "command.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CommandListPayload

    query: str | None = None
    domain: str | None = None
    authority: Authority | None = None
    dependency_tier: DependencyTier | None = None
    locality: Locality | None = None
    effect_class: EffectClass | None = None
    projection: Projection | None = None
    cursor: str | None = None
    page_size: int = 100

    def __post_init__(self) -> None:
        for name in ("query", "domain", "cursor"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"command list {name} must be a non-empty string")
        if self.domain is not None:
            try:
                validate_command_id(f"{self.domain}.list")
            except ValueError as exc:
                raise ValueError("command list domain must be a canonical noun") from exc
        if self.cursor is not None:
            validate_command_id(self.cursor)
        enum_fields = {
            "authority": Authority,
            "dependency_tier": DependencyTier,
            "locality": Locality,
            "effect_class": EffectClass,
            "projection": Projection,
        }
        for name, enum_type in enum_fields.items():
            value = getattr(self, name)
            if value is not None and not isinstance(value, enum_type):
                raise ValueError(f"command list {name} must use {enum_type.__name__}")
        if (
            not isinstance(self.page_size, int)
            or isinstance(self.page_size, bool)
            or not 1 <= self.page_size <= 500
        ):
            raise ValueError("command list page_size must be between 1 and 500")


@dataclass(frozen=True, slots=True)
class CommandDescribeRequest:
    COMMAND_ID: ClassVar[str] = "command.describe"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CommandDescriptionPayload

    target_command_id: str

    def __post_init__(self) -> None:
        validate_command_id(self.target_command_id)


@dataclass(frozen=True, slots=True)
class InvocationReadRequest:
    COMMAND_ID: ClassVar[str] = "invocation.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = InvocationReadPayload

    reference: OutcomeReference

    def __post_init__(self) -> None:
        if not isinstance(self.reference, OutcomeReference):
            raise ValueError("invocation.read reference must be an OutcomeReference")


CommandRequest = (
    CommandListRequest
    | CommandDescribeRequest
    | InvocationReadRequest
    | ArtefactReadRequest
    | ArtefactReadArchivedRequest
    | ArtefactOutlineRequest
    | ArtefactListRequest
    | ArtefactListArchivedRequest
    | LinksCheckRequest
    | MemoryListRequest
    | MemoryReadRequest
    | PluginListRequest
    | PluginReadRequest
    | RuntimeReadEnvironmentRequest
    | SessionStartRequest
    | SkillListRequest
    | SkillReadRequest
    | StyleListRequest
    | StyleReadRequest
    | TemplateListRequest
    | TemplateReadRequest
    | TriggerListRequest
    | TriggerReadRequest
    | ArtefactTypeListRequest
    | ArtefactTypeReadRequest
    | TypeStatusRequest
    | VaultCheckRequest
    | VaultReadConfigRequest
    | VaultReadRouterRequest
    | VaultReadFileRequest
    | WorkspaceListRequest
    | WorkspaceReadRequest
    | WorkspaceResolveRequest
)


def command_identity(request: CommandRequest) -> tuple[str, int, type]:
    """Return identity owned by the concrete request type, never caller input."""
    request_type = type(request)
    if request_type not in {
        CommandListRequest,
        CommandDescribeRequest,
        InvocationReadRequest,
        ArtefactReadRequest,
        ArtefactReadArchivedRequest,
        ArtefactOutlineRequest,
        ArtefactListRequest,
        ArtefactListArchivedRequest,
        LinksCheckRequest,
        MemoryListRequest,
        MemoryReadRequest,
        PluginListRequest,
        PluginReadRequest,
        RuntimeReadEnvironmentRequest,
        SessionStartRequest,
        SkillListRequest,
        SkillReadRequest,
        StyleListRequest,
        StyleReadRequest,
        TemplateListRequest,
        TemplateReadRequest,
        TriggerListRequest,
        TriggerReadRequest,
        ArtefactTypeListRequest,
        ArtefactTypeReadRequest,
        TypeStatusRequest,
        VaultCheckRequest,
        VaultReadConfigRequest,
        VaultReadRouterRequest,
        VaultReadFileRequest,
        WorkspaceListRequest,
        WorkspaceReadRequest,
        WorkspaceResolveRequest,
    }:
        raise TypeError(f"unregistered command request type: {request_type.__name__}")
    return request_type.COMMAND_ID, request_type.COMMAND_VERSION, request_type.RESULT_TYPE
