"""Sealed typed requests for foundational application commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ._named_edit_requests import NAMED_EDIT_REQUEST_TYPES, NamedEditRequest
from .artefact.append import ArtefactAppendRequest
from .artefact.archive import ArtefactArchiveRequest
from .artefact.convert import ArtefactConvertRequest
from .artefact.create import ArtefactCreateRequest
from .artefact.delete import ArtefactDeleteRequest
from .artefact.delete_section import ArtefactDeleteSectionRequest
from .artefact.edit import ArtefactEditRequest
from .artefact.list import ArtefactListRequest
from .artefact.list_archived import ArtefactListArchivedRequest
from .artefact.migrate_naming import ArtefactMigrateNamingRequest
from .artefact.outline import ArtefactOutlineRequest
from .artefact.prepend import ArtefactPrependRequest
from .artefact.read import ArtefactReadRequest
from .artefact.read_archived import ArtefactReadArchivedRequest
from .artefact.repair_frontmatter import ArtefactRepairFrontmatterRequest
from .artefact.repair_ownership import ArtefactRepairOwnershipRequest
from .artefact.reparent import ArtefactReparentRequest
from .artefact.rename import ArtefactRenameRequest
from .artefact.reparent_children import ArtefactReparentChildrenRequest
from .artefact.replace_text import ArtefactReplaceTextRequest
from .artefact.search import ArtefactSearchRequest
from .artefact.set_key import ArtefactSetKeyRequest
from .artefact.set_naming_field import ArtefactSetNamingFieldRequest
from .artefact.set_status import ArtefactSetStatusRequest
from .artefact.unarchive import ArtefactUnarchiveRequest
from .attachment.upload import AttachmentUploadRequest
from .content.classify import ContentClassifyRequest
from .content.ingest import ContentIngestRequest
from .content.resolve import ContentResolveRequest
from .links.check import LinksCheckRequest
from .links.fix import LinksFixRequest
from .memory.list import MemoryListRequest
from .memory.create import MemoryCreateRequest
from .memory.read import MemoryReadRequest
from .memory.search import MemorySearchRequest
from .plugin.create import PluginCreateRequest
from .plugin.list import PluginListRequest
from .plugin.read import PluginReadRequest
from .plugin.replace import PluginReplaceRequest
from .plugin.search import PluginSearchRequest
from .retrieval.construct_benchmark import RetrievalConstructBenchmarkRequest
from .retrieval.enable import RetrievalEnableRequest
from .retrieval.evaluate import RetrievalEvaluateRequest
from .retrieval.rebuild_lexical import RetrievalRebuildLexicalRequest
from .retrieval.rebuild_semantic import RetrievalRebuildSemanticRequest
from .retrieval.repair_lexical import RetrievalRepairLexicalRequest
from .retrieval.repair_semantic import RetrievalRepairSemanticRequest
from .runtime.rebuild_router import RuntimeRebuildRouterRequest
from .runtime.read_environment import RuntimeReadEnvironmentRequest
from .runtime.repair_router import RuntimeRepairRouterRequest
from .session.start import SessionStartRequest
from .shaping.render_presentation import ShapingRenderPresentationRequest
from .shaping.render_printable import ShapingRenderPrintableRequest
from .shaping.start import ShapingStartRequest
from .skill.list import SkillListRequest
from .skill.create import SkillCreateRequest
from .skill.read import SkillReadRequest
from .skill.search import SkillSearchRequest
from .stage.create import StageCreateRequest
from .stage.discard import StageDiscardRequest
from .style.list import StyleListRequest
from .style.create import StyleCreateRequest
from .style.read import StyleReadRequest
from .style.search import StyleSearchRequest
from .template.list import TemplateListRequest
from .template.create import TemplateCreateRequest
from .template.read import TemplateReadRequest
from .trigger.create import TriggerCreateRequest
from .trigger.delete import TriggerDeleteRequest
from .trigger.list import TriggerListRequest
from .trigger.read import TriggerReadRequest
from .trigger.replace import TriggerReplaceRequest
from .trigger.search import TriggerSearchRequest
from .type.create import TypeCreateRequest
from .type.install import TypeInstallRequest
from .type.list import ArtefactTypeListRequest
from .type.read import ArtefactTypeReadRequest
from .type.replace import TypeReplaceRequest
from .type.status import TypeStatusRequest
from .type.sync import TypeSyncRequest
from .vault.check import VaultCheckRequest
from .vault.read_config import VaultReadConfigRequest
from .vault.read_router import VaultReadRouterRequest
from .vault.read_file import VaultReadFileRequest
from .workspace.bind import WorkspaceBindRequest
from .workspace.configure_bootstrap import WorkspaceConfigureBootstrapRequest
from .workspace.list import WorkspaceListRequest
from .workspace.read import WorkspaceReadRequest
from .workspace.register import WorkspaceRegisterRequest
from .workspace.repair_registry import WorkspaceRepairRegistryRequest
from .workspace.resolve import WorkspaceResolveRequest
from .workspace.setup import WorkspaceSetupRequest
from .workspace.unregister import WorkspaceUnregisterRequest
from .workspace.update_metadata import WorkspaceUpdateMetadataRequest
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
    | ArtefactAppendRequest
    | ArtefactArchiveRequest
    | ArtefactConvertRequest
    | ArtefactCreateRequest
    | ArtefactDeleteRequest
    | ArtefactDeleteSectionRequest
    | ArtefactEditRequest
    | ArtefactReadRequest
    | ArtefactReadArchivedRequest
    | ArtefactRepairFrontmatterRequest
    | ArtefactRepairOwnershipRequest
    | ArtefactReparentRequest
    | ArtefactRenameRequest
    | ArtefactReparentChildrenRequest
    | ArtefactOutlineRequest
    | ArtefactPrependRequest
    | ArtefactReplaceTextRequest
    | ArtefactListRequest
    | ArtefactListArchivedRequest
    | ArtefactMigrateNamingRequest
    | ArtefactSearchRequest
    | ArtefactSetKeyRequest
    | ArtefactSetNamingFieldRequest
    | ArtefactSetStatusRequest
    | ArtefactUnarchiveRequest
    | AttachmentUploadRequest
    | ContentClassifyRequest
    | ContentIngestRequest
    | ContentResolveRequest
    | LinksCheckRequest
    | LinksFixRequest
    | MemoryCreateRequest
    | NamedEditRequest
    | MemoryListRequest
    | MemoryReadRequest
    | MemorySearchRequest
    | PluginCreateRequest
    | PluginListRequest
    | PluginReadRequest
    | PluginReplaceRequest
    | PluginSearchRequest
    | RetrievalConstructBenchmarkRequest
    | RetrievalEnableRequest
    | RetrievalEvaluateRequest
    | RetrievalRebuildLexicalRequest
    | RetrievalRebuildSemanticRequest
    | RetrievalRepairLexicalRequest
    | RetrievalRepairSemanticRequest
    | RuntimeRebuildRouterRequest
    | RuntimeReadEnvironmentRequest
    | RuntimeRepairRouterRequest
    | SessionStartRequest
    | ShapingRenderPresentationRequest
    | ShapingRenderPrintableRequest
    | ShapingStartRequest
    | SkillCreateRequest
    | SkillListRequest
    | SkillReadRequest
    | SkillSearchRequest
    | StageCreateRequest
    | StageDiscardRequest
    | StyleCreateRequest
    | StyleListRequest
    | StyleReadRequest
    | StyleSearchRequest
    | TemplateCreateRequest
    | TemplateListRequest
    | TemplateReadRequest
    | TriggerCreateRequest
    | TriggerDeleteRequest
    | TriggerListRequest
    | TriggerReadRequest
    | TriggerReplaceRequest
    | TriggerSearchRequest
    | TypeCreateRequest
    | TypeInstallRequest
    | ArtefactTypeListRequest
    | ArtefactTypeReadRequest
    | TypeReplaceRequest
    | TypeStatusRequest
    | TypeSyncRequest
    | VaultCheckRequest
    | VaultReadConfigRequest
    | VaultReadRouterRequest
    | VaultReadFileRequest
    | WorkspaceBindRequest
    | WorkspaceConfigureBootstrapRequest
    | WorkspaceListRequest
    | WorkspaceReadRequest
    | WorkspaceRegisterRequest
    | WorkspaceRepairRegistryRequest
    | WorkspaceResolveRequest
    | WorkspaceSetupRequest
    | WorkspaceUnregisterRequest
    | WorkspaceUpdateMetadataRequest
)


def command_identity(request: CommandRequest) -> tuple[str, int, type]:
    """Return identity owned by the concrete request type, never caller input."""
    request_type = type(request)
    if request_type not in {
        CommandListRequest,
        CommandDescribeRequest,
        InvocationReadRequest,
        ArtefactAppendRequest,
        ArtefactArchiveRequest,
        ArtefactConvertRequest,
        ArtefactCreateRequest,
        ArtefactDeleteRequest,
        ArtefactDeleteSectionRequest,
        ArtefactEditRequest,
        ArtefactReadRequest,
        ArtefactReadArchivedRequest,
        ArtefactRepairFrontmatterRequest,
        ArtefactRepairOwnershipRequest,
        ArtefactReparentRequest,
        ArtefactRenameRequest,
        ArtefactReparentChildrenRequest,
        ArtefactOutlineRequest,
        ArtefactPrependRequest,
        ArtefactReplaceTextRequest,
        ArtefactListRequest,
        ArtefactListArchivedRequest,
        ArtefactMigrateNamingRequest,
        ArtefactSearchRequest,
        ArtefactSetKeyRequest,
        ArtefactSetNamingFieldRequest,
        ArtefactSetStatusRequest,
        ArtefactUnarchiveRequest,
        AttachmentUploadRequest,
        ContentClassifyRequest,
        ContentIngestRequest,
        ContentResolveRequest,
        LinksCheckRequest,
        LinksFixRequest,
        MemoryCreateRequest,
        *NAMED_EDIT_REQUEST_TYPES,
        MemoryListRequest,
        MemoryReadRequest,
        MemorySearchRequest,
        PluginCreateRequest,
        PluginListRequest,
        PluginReadRequest,
        PluginReplaceRequest,
        PluginSearchRequest,
        RetrievalConstructBenchmarkRequest,
        RetrievalEnableRequest,
        RetrievalEvaluateRequest,
        RetrievalRebuildLexicalRequest,
        RetrievalRebuildSemanticRequest,
        RetrievalRepairLexicalRequest,
        RetrievalRepairSemanticRequest,
        RuntimeRebuildRouterRequest,
        RuntimeReadEnvironmentRequest,
        RuntimeRepairRouterRequest,
        SessionStartRequest,
        ShapingRenderPresentationRequest,
        ShapingRenderPrintableRequest,
        ShapingStartRequest,
        SkillCreateRequest,
        SkillListRequest,
        SkillReadRequest,
        SkillSearchRequest,
        StageCreateRequest,
        StageDiscardRequest,
        StyleCreateRequest,
        StyleListRequest,
        StyleReadRequest,
        StyleSearchRequest,
        TemplateCreateRequest,
        TemplateListRequest,
        TemplateReadRequest,
        TriggerCreateRequest,
        TriggerDeleteRequest,
        TriggerListRequest,
        TriggerReadRequest,
        TriggerReplaceRequest,
        TriggerSearchRequest,
        TypeCreateRequest,
        TypeInstallRequest,
        ArtefactTypeListRequest,
        ArtefactTypeReadRequest,
        TypeReplaceRequest,
        TypeStatusRequest,
        TypeSyncRequest,
        VaultCheckRequest,
        VaultReadConfigRequest,
        VaultReadRouterRequest,
        VaultReadFileRequest,
        WorkspaceBindRequest,
        WorkspaceConfigureBootstrapRequest,
        WorkspaceListRequest,
        WorkspaceReadRequest,
        WorkspaceRegisterRequest,
        WorkspaceRepairRegistryRequest,
        WorkspaceResolveRequest,
        WorkspaceSetupRequest,
        WorkspaceUnregisterRequest,
        WorkspaceUpdateMetadataRequest,
    }:
        raise TypeError(f"unregistered command request type: {request_type.__name__}")
    return request_type.COMMAND_ID, request_type.COMMAND_VERSION, request_type.RESULT_TYPE
