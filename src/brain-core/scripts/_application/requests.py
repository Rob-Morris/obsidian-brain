"""Sealed typed requests for foundational application commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .artefact.archive import ArtefactArchiveRequest
from .artefact.convert import ArtefactConvertRequest
from .artefact.create import ArtefactCreateRequest
from .artefact.delete import ArtefactDeleteRequest
from .artefact.list import ArtefactListRequest
from .artefact.migrate_naming import ArtefactMigrateNamingRequest
from .artefact.outline import ArtefactOutlineRequest
from .artefact.read import ArtefactReadRequest
from .artefact.repair import ArtefactRepairRequest
from .artefact.reparent import ArtefactReparentRequest
from .artefact.rename import ArtefactRenameRequest
from .artefact.reparent_children import ArtefactReparentChildrenRequest
from .artefact.search import ArtefactSearchRequest
from .artefact.set_key import ArtefactSetKeyRequest
from .artefact.set_naming_field import ArtefactSetNamingFieldRequest
from .artefact.set_status import ArtefactSetStatusRequest
from .artefact.unarchive import ArtefactUnarchiveRequest
from .attachment.upload import AttachmentUploadRequest
from .content.classify import ContentClassifyRequest
from .content.ingest import ContentIngestRequest
from .content.resolve import ContentResolveRequest
from .document.edit import DocumentEditRequest
from .links.check import LinksCheckRequest
from .links.fix import LinksFixRequest
from .plugin.create import PluginCreateRequest
from .plugin.replace import PluginReplaceRequest
from .resource.create import ResourceCreateRequest
from .resource.list import ResourceListRequest
from .resource.read import ResourceReadRequest
from .resource.search import ResourceSearchRequest
from .retrieval.construct_benchmark import RetrievalConstructBenchmarkRequest
from .retrieval.enable import RetrievalEnableRequest
from .retrieval.evaluate import RetrievalEvaluateRequest
from .retrieval.refresh_lexical import RetrievalRefreshLexicalRequest
from .retrieval.rebuild_semantic import RetrievalRebuildSemanticRequest
from .retrieval.repair_semantic import RetrievalRepairSemanticRequest
from .runtime.refresh_router import RuntimeRefreshRouterRequest
from .runtime.read_environment import RuntimeReadEnvironmentRequest
from .runtime.status import RuntimeStatusRequest
from .runtime.warmup import RuntimeWarmupRequest
from .session.start import SessionStartRequest
from .shaping.render import ShapingRenderRequest
from .shaping.start import ShapingStartRequest
from .stage.create import StageCreateRequest
from .stage.discard import StageDiscardRequest
from .trigger.create import TriggerCreateRequest
from .trigger.delete import TriggerDeleteRequest
from .trigger.replace import TriggerReplaceRequest
from .type.create import TypeCreateRequest
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
from .workspace.setup import WorkspaceSetupRequest
from .workspace.unregister import WorkspaceUnregisterRequest
from .workspace.update_metadata import WorkspaceUpdateMetadataRequest
from .receipts import OutcomeReceipt, OutcomeReference, ReceiptLookupState
from .types import (
    Availability,
    Authority,
    CommandLifecycle,
    CommandOwner,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
    SnapshotFreshness,
    validate_command_id,
)


@dataclass(frozen=True, slots=True)
class CatalogueCursor:
    snapshot_token: str
    command_id: str

    def __post_init__(self) -> None:
        if not self.snapshot_token.strip():
            raise ValueError("catalogue cursor requires a snapshot token")
        validate_command_id(self.command_id)


@dataclass(frozen=True, slots=True)
class CommandSummary:
    command_id: str
    command_version: int
    owner: CommandOwner
    summary: str
    projections: tuple[ProjectionEligibility, ...]
    dependency_tier: DependencyTier
    locality: Locality
    authority: Authority
    effect_class: EffectClass
    retry_class: RetryClass
    required_providers: tuple[str, ...]
    optional_providers: tuple[str, ...]
    availability: Availability
    availability_freshness: SnapshotFreshness
    missing_optional_providers: tuple[str, ...]
    lifecycle: CommandLifecycle
    replacement_command_id: str | None

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1 or not self.summary.strip():
            raise ValueError("command summary requires identity, version and summary")
        if self.replacement_command_id is not None:
            validate_command_id(self.replacement_command_id)


@dataclass(frozen=True, slots=True)
class CommandListPayload:
    catalogue_schema: str
    catalogue_fingerprint: str
    entries: tuple[CommandSummary, ...]
    snapshot_token: str
    availability_freshness: SnapshotFreshness
    next_cursor: CatalogueCursor | None = None

    def __post_init__(self) -> None:
        if not self.catalogue_schema.startswith("brain.command-catalogue/"):
            raise ValueError("command list payload requires the application catalogue schema")
        if not self.catalogue_fingerprint.startswith("sha256:"):
            raise ValueError("command list payload requires the application catalogue fingerprint")
        command_ids = self.command_ids
        if command_ids != tuple(sorted(command_ids)):
            raise ValueError("command list payload identifiers must be sorted")
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command list payload identifiers must be unique")
        if not self.snapshot_token.strip():
            raise ValueError("command list payload requires a snapshot token")
        if self.next_cursor is not None:
            if not command_ids or self.next_cursor.command_id != command_ids[-1]:
                raise ValueError("command list next_cursor must identify the final page item")
            if self.next_cursor.snapshot_token != self.snapshot_token:
                raise ValueError("command list cursor must retain the availability snapshot")

    @property
    def command_ids(self) -> tuple[str, ...]:
        return tuple(entry.command_id for entry in self.entries)


@dataclass(frozen=True, slots=True)
class CommandExample:
    label: str
    mcp_tool: str
    cli_argv: tuple[str, str]
    request_json: str

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("command example requires a label and MCP tool")
        validate_command_id(self.mcp_tool)
        if len(self.cli_argv) != 2 or any(not item.strip() for item in self.cli_argv):
            raise ValueError("command example requires canonical CLI noun and verb")


@dataclass(frozen=True, slots=True)
class ResultVariantContract:
    status: str
    description: str

    def __post_init__(self) -> None:
        if self.status not in {"ok", "partial", "error"} or not self.description.strip():
            raise ValueError("result variant contract is invalid")


@dataclass(frozen=True, slots=True)
class CommandDescriptionPayload:
    catalogue_schema: str
    catalogue_fingerprint: str
    command_id: str
    command_version: int
    owner: CommandOwner
    summary: str
    request_schema_json: str
    result_type: str
    result_schema_json: str
    result_variants: tuple[ResultVariantContract, ...]
    error_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    dependency_tier: DependencyTier
    locality: Locality
    required_providers: tuple[str, ...]
    optional_providers: tuple[str, ...]
    availability: Availability
    availability_freshness: SnapshotFreshness
    authority: Authority
    effect_class: EffectClass
    retry_class: RetryClass
    projections: tuple[ProjectionEligibility, ...]
    examples: tuple[CommandExample, ...]
    lifecycle: CommandLifecycle
    replacement_command_id: str | None

    def __post_init__(self) -> None:
        if not self.catalogue_schema.startswith("brain.command-catalogue/"):
            raise ValueError("command description requires the application catalogue schema")
        if not self.catalogue_fingerprint.startswith("sha256:"):
            raise ValueError("command description requires the application catalogue fingerprint")
        validate_command_id(self.command_id)
        if self.command_version < 1 or not self.summary.strip():
            raise ValueError("command description requires a version and summary")
        if not self.request_schema_json or not self.result_schema_json:
            raise ValueError("command description requires request and result schemas")
        if not self.result_type.strip() or not self.result_variants or not self.examples:
            raise ValueError("command description requires result and example contracts")
        if self.replacement_command_id is not None:
            validate_command_id(self.replacement_command_id)


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
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = CommandListPayload

    query: str | None = None
    domain: str | None = None
    owner: CommandOwner | None = None
    availability: Availability | None = None
    authority: Authority | None = None
    dependency_tier: DependencyTier | None = None
    locality: Locality | None = None
    effect_class: EffectClass | None = None
    retry_class: RetryClass | None = None
    projection: Projection | None = None
    cursor: CatalogueCursor | None = None
    refresh: bool = False
    page_size: int = 100

    def __post_init__(self) -> None:
        for name in ("query", "domain"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"command list {name} must be a non-empty string")
        if self.domain is not None:
            try:
                validate_command_id(f"{self.domain}.list")
            except ValueError as exc:
                raise ValueError("command list domain must be a canonical noun") from exc
        enum_fields = {
            "owner": CommandOwner,
            "availability": Availability,
            "authority": Authority,
            "dependency_tier": DependencyTier,
            "locality": Locality,
            "effect_class": EffectClass,
            "retry_class": RetryClass,
            "projection": Projection,
        }
        for name, enum_type in enum_fields.items():
            value = getattr(self, name)
            if value is not None and not isinstance(value, enum_type):
                raise ValueError(f"command list {name} must use {enum_type.__name__}")
        if self.cursor is not None and not isinstance(self.cursor, CatalogueCursor):
            raise ValueError("command list cursor must be a CatalogueCursor")
        if not isinstance(self.refresh, bool):
            raise ValueError("command list refresh must be boolean")
        if self.refresh and self.cursor is not None:
            raise ValueError("command list refresh cannot be combined with pagination")
        if (
            not isinstance(self.page_size, int)
            or isinstance(self.page_size, bool)
            or not 1 <= self.page_size <= 500
        ):
            raise ValueError("command list page_size must be between 1 and 500")


@dataclass(frozen=True, slots=True)
class CommandDescribeRequest:
    COMMAND_ID: ClassVar[str] = "command.describe"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = CommandDescriptionPayload
    MINIMAL_EXAMPLE: ClassVar[dict[str, str]] = {
        "target_command_id": "command.list"
    }

    target_command_id: str

    def __post_init__(self) -> None:
        validate_command_id(self.target_command_id)


@dataclass(frozen=True, slots=True)
class InvocationReadRequest:
    COMMAND_ID: ClassVar[str] = "invocation.read"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = InvocationReadPayload
    MINIMAL_EXAMPLE: ClassVar[dict[str, str]] = {"invocation_id": "example"}

    invocation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise ValueError("invocation.read invocation_id must be non-empty")

    @property
    def reference(self) -> OutcomeReference:
        return OutcomeReference(self.invocation_id)


CommandRequest = (
    CommandListRequest
    | CommandDescribeRequest
    | InvocationReadRequest
    | ArtefactArchiveRequest
    | ArtefactConvertRequest
    | ArtefactCreateRequest
    | ArtefactDeleteRequest
    | ArtefactReadRequest
    | ArtefactRepairRequest
    | ArtefactReparentRequest
    | ArtefactRenameRequest
    | ArtefactReparentChildrenRequest
    | ArtefactOutlineRequest
    | ArtefactListRequest
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
    | DocumentEditRequest
    | LinksCheckRequest
    | LinksFixRequest
    | PluginCreateRequest
    | PluginReplaceRequest
    | ResourceCreateRequest
    | ResourceListRequest
    | ResourceReadRequest
    | ResourceSearchRequest
    | RetrievalConstructBenchmarkRequest
    | RetrievalEnableRequest
    | RetrievalEvaluateRequest
    | RetrievalRefreshLexicalRequest
    | RetrievalRebuildSemanticRequest
    | RetrievalRepairSemanticRequest
    | RuntimeRefreshRouterRequest
    | RuntimeReadEnvironmentRequest
    | RuntimeStatusRequest
    | RuntimeWarmupRequest
    | SessionStartRequest
    | ShapingRenderRequest
    | ShapingStartRequest
    | StageCreateRequest
    | StageDiscardRequest
    | TriggerCreateRequest
    | TriggerDeleteRequest
    | TriggerReplaceRequest
    | TypeCreateRequest
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
        ArtefactArchiveRequest,
        ArtefactConvertRequest,
        ArtefactCreateRequest,
        ArtefactDeleteRequest,
        ArtefactReadRequest,
        ArtefactRepairRequest,
        ArtefactReparentRequest,
        ArtefactRenameRequest,
        ArtefactReparentChildrenRequest,
        ArtefactOutlineRequest,
        ArtefactListRequest,
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
        DocumentEditRequest,
        LinksCheckRequest,
        LinksFixRequest,
        PluginCreateRequest,
        PluginReplaceRequest,
        ResourceCreateRequest,
        ResourceListRequest,
        ResourceReadRequest,
        ResourceSearchRequest,
        RetrievalConstructBenchmarkRequest,
        RetrievalEnableRequest,
        RetrievalEvaluateRequest,
        RetrievalRefreshLexicalRequest,
        RetrievalRebuildSemanticRequest,
        RetrievalRepairSemanticRequest,
        RuntimeRefreshRouterRequest,
        RuntimeReadEnvironmentRequest,
        RuntimeStatusRequest,
        RuntimeWarmupRequest,
        SessionStartRequest,
        ShapingRenderRequest,
        ShapingStartRequest,
        StageCreateRequest,
        StageDiscardRequest,
        TriggerCreateRequest,
        TriggerDeleteRequest,
        TriggerReplaceRequest,
        TypeCreateRequest,
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
        WorkspaceSetupRequest,
        WorkspaceUnregisterRequest,
        WorkspaceUpdateMetadataRequest,
    }:
        raise TypeError(f"unregistered command request type: {request_type.__name__}")
    return request_type.COMMAND_ID, request_type.COMMAND_VERSION, request_type.RESULT_TYPE
