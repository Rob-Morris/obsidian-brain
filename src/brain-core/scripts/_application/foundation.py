"""Foundational discovery and invocation-outcome command owners."""

from __future__ import annotations

from ._decoding import reject_unexpected
from ._response_budget import MODEL_TEXT_BUDGET, encoded_result_size

import json
from typing import Mapping

from .catalogue import (
    ALL_APPLICATION_PROJECTIONS,
    ApplicationCatalogue,
    ApplicationEntry,
    type_identity,
)
from .context import InvocationContext
from .receipts import ReceiptLookupState
from .requests import (
    CatalogueCursor,
    CommandDescribeRequest,
    CommandDescriptionPayload,
    CommandExample,
    CommandListPayload,
    CommandListRequest,
    CommandRequest,
    CommandSummary,
    CommandBrief,
    CommandAccess,
    CommandListView,
    InvocationReadPayload,
    InvocationReadRequest,
    ResultVariantContract,
)
from .projection import minimal_request_payload, project_identity, request_schema, result_payload_schema
from .resolver import RequestResolver, ResolverEntry
from .results import (
    CapabilityUnavailableDetails,
    CommandError,
    Error,
    ErrorCode,
    Ok,
    RequestErrorDetails,
    WarningCode,
)
from .types import (
    Availability,
    Authority,
    CommandOwner,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    RetryClass,
)


class _FoundationOwners:
    def __init__(self) -> None:
        self._catalogue: ApplicationCatalogue | None = None

    def bind(self, catalogue: ApplicationCatalogue) -> None:
        if self._catalogue is not None:
            raise RuntimeError("foundation command owners are already bound")
        self._catalogue = catalogue

    def command_list(self, context: InvocationContext, request: CommandRequest):
        if type(request) is not CommandListRequest:
            raise TypeError("command.list owner received the wrong request type")
        snapshot = self._snapshot(context, request)
        if isinstance(snapshot, Error):
            return snapshot
        catalogue = self._require_catalogue()
        entries = tuple(
            entry
            for entry in catalogue.entries
            if self._matches_list_filter(entry, request, context, snapshot)
        )
        start = 0
        if request.cursor is not None:
            cursor_index = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if entry.command_id == request.cursor.command_id
                ),
                None,
            )
            if cursor_index is None:
                return Error(
                    CommandListRequest.COMMAND_ID,
                    CommandListRequest.COMMAND_VERSION,
                    CommandError(
                        ErrorCode.INVALID_REQUEST,
                        "The command-list cursor is not valid for these filters.",
                        RequestErrorDetails(
                            "cursor",
                            "cursor was not present in the filtered catalogue",
                        ),
                    ),
                )
            start = cursor_index + 1
        # Bound the canonical envelope, before any adapter duplicates it as
        # JSON text and structured content. Keep room for host status text.
        access = context.authority.observe()
        fingerprint = catalogue.fingerprint
        page = []

        def result_for(items):
            has_more = start + len(items) < len(entries)
            cursor = (
                CatalogueCursor(snapshot.token, items[-1].command_id)
                if items and has_more else None
            )
            return Ok(
                request.COMMAND_ID, request.COMMAND_VERSION,
                CommandListPayload(
                    catalogue.schema, fingerprint, tuple(items),
                    snapshot.token, snapshot.freshness, cursor,
                ),
            )

        result = result_for(page)
        for entry in entries[start : start + request.page_size]:
            item = (
                self._summary(entry, context, snapshot, access)
                if request.view is CommandListView.DETAILED
                else self._brief(entry, context, snapshot, access)
            )
            candidate = result_for([*page, item])
            if encoded_result_size(candidate) > MODEL_TEXT_BUDGET:
                if not page:
                    return Error(request.COMMAND_ID, request.COMMAND_VERSION,
                        CommandError(ErrorCode.INVALID_REQUEST,
                            "This command exceeds the discovery page budget; use command.describe.",
                            RequestErrorDetails("view", entry.command_id)))
                break
            page.append(item)
            result = candidate
        return result

    def command_describe(self, context: InvocationContext, request: CommandRequest):
        if type(request) is not CommandDescribeRequest:
            raise TypeError("command.describe owner received the wrong request type")
        entry = next(
            (
                candidate
                for candidate in self._require_catalogue().entries
                if candidate.command_id == request.target_command_id
                and _ceiling_allows(context, candidate.command_id)
            ),
            None,
        )
        if entry is None:
            return Error(
                CommandDescribeRequest.COMMAND_ID,
                CommandDescribeRequest.COMMAND_VERSION,
                CommandError(
                    ErrorCode.NOT_FOUND,
                    "The requested command is not installed in the selected Brain.",
                ),
            )
        return Ok(
            CommandDescribeRequest.COMMAND_ID,
            CommandDescribeRequest.COMMAND_VERSION,
            self._description(entry, context),
        )

    def invocation_read(self, context: InvocationContext, request: CommandRequest):
        if type(request) is not InvocationReadRequest:
            raise TypeError("invocation.read owner received the wrong request type")
        receipt = context.receipt_reader.read(request.reference)
        state = (
            ReceiptLookupState.FOUND
            if receipt is not None
            else ReceiptLookupState.STILL_UNKNOWN
        )
        return Ok(
            InvocationReadRequest.COMMAND_ID,
            InvocationReadRequest.COMMAND_VERSION,
            InvocationReadPayload(request.reference, state, receipt),
        )

    def _require_catalogue(self) -> ApplicationCatalogue:
        if self._catalogue is None:
            raise RuntimeError("foundation command owners are not bound")
        return self._catalogue

    @staticmethod
    def _matches_list_filter(
        entry: ApplicationEntry,
        request: CommandListRequest,
        context: InvocationContext,
        snapshot,
    ) -> bool:
        if not _ceiling_allows(context, entry.command_id):
            return False
        if request.owner is not None and request.owner is not CommandOwner.APPLICATION:
            return False
        if (
            request.query is not None
            and request.query.casefold()
            not in f"{entry.command_id} {entry.summary}".casefold()
        ):
            return False
        if (
            request.domain is not None
            and entry.command_id.split(".", 1)[0] != request.domain
        ):
            return False
        if request.authority is not None and entry.authority is not request.authority:
            return False
        if (
            request.dependency_tier is not None
            and entry.dependency_tier is not request.dependency_tier
        ):
            return False
        if request.locality is not None and entry.locality is not request.locality:
            return False
        if (
            request.effect_class is not None
            and entry.effect_class is not request.effect_class
        ):
            return False
        if request.retry_class is not None and entry.retry_class is not request.retry_class:
            return False
        if (
            request.projection is not None
            and request.projection not in entry.eligible_projections
        ):
            return False
        if (
            request.availability is not None
            and _availability(entry, context, snapshot) is not request.availability
        ):
            return False
        return True

    def _snapshot(self, context: InvocationContext, request: CommandListRequest):
        if request.refresh:
            if context.capability_snapshots is None:
                details = CapabilityUnavailableDetails(
                    DependencyTier.PORTABLE,
                    context.dependency_tier,
                    Locality.SELECTED_BRAIN_LOCAL,
                    ("provider:capability_snapshots",),
                    context.capabilities.freshness,
                    True,
                )
                return Error(
                    request.COMMAND_ID,
                    request.COMMAND_VERSION,
                    CommandError(
                        ErrorCode.CAPABILITY_UNAVAILABLE,
                        "Capability refresh is not available in this adapter.",
                        details,
                    ),
                )
            provider_ids = tuple(
                sorted(
                    {
                        provider_id
                        for entry in self._require_catalogue().entries
                        for provider_id in (*entry.required_providers, *entry.optional_providers)
                    }
                )
            )
            return context.capability_snapshots.refresh(
                provider_ids,
                previous_token=context.capabilities.token,
            )
        if request.cursor is None:
            return context.capabilities
        if request.cursor.snapshot_token == context.capabilities.token:
            return context.capabilities
        if context.capability_snapshots is not None:
            snapshot = context.capability_snapshots.read(request.cursor.snapshot_token)
            if snapshot is not None:
                return snapshot
        return Error(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.INVALID_REQUEST,
                "The command-list availability snapshot has expired.",
                RequestErrorDetails("cursor", "snapshot token is no longer retained"),
            ),
        )

    @staticmethod
    def _brief(entry: ApplicationEntry, context: InvocationContext, snapshot, access):
        return CommandBrief(
            entry.command_id, entry.command_version, entry.summary,
            entry.authority, entry.effect_class,
            _availability(entry, context, snapshot), _access(entry, access),
        )

    @staticmethod
    def _summary(entry: ApplicationEntry, context: InvocationContext, snapshot, access):
        missing_optional = tuple(
            provider_id
            for provider_id in entry.optional_providers
            if (
                context.providers.get(provider_id) is None
                or snapshot.availability_of(provider_id) is not Availability.AVAILABLE
            )
        )
        return CommandSummary(
            entry.command_id,
            entry.command_version,
            CommandOwner.APPLICATION,
            entry.summary,
            entry.projections,
            entry.dependency_tier,
            entry.locality,
            entry.authority,
            entry.effect_class,
            entry.retry_class,
            entry.required_providers,
            entry.optional_providers,
            _availability(entry, context, snapshot),
            snapshot.freshness,
            missing_optional,
            entry.lifecycle,
            entry.replacement_command_id,
            _access(entry, access),
        )

    def _description(self, entry: ApplicationEntry, context: InvocationContext):
        access = context.authority.observe()
        identity = project_identity(entry.command_id)
        result_variants = [
            ResultVariantContract("ok", "The command completed with a typed result."),
            ResultVariantContract("error", "The command completed without a typed result."),
        ]
        error_codes = [code.value for code in ErrorCode]
        if entry.effect_class is not EffectClass.NONE:
            result_variants.insert(
                1,
                ResultVariantContract(
                    "partial",
                    "A known subset committed and is enumerated in committed effects.",
                ),
            )
        example_payload = minimal_request_payload(entry.request_type)
        return CommandDescriptionPayload(
            self._require_catalogue().schema,
            self._require_catalogue().fingerprint,
            entry.command_id,
            entry.command_version,
            CommandOwner.APPLICATION,
            entry.summary,
            json.dumps(request_schema(entry.request_type), separators=(",", ":")),
            type_identity(entry.result_type),
            json.dumps(result_payload_schema(entry.request_type), separators=(",", ":")),
            tuple(result_variants),
            tuple(error_codes),
            tuple(code.value for code in WarningCode),
            entry.dependency_tier,
            entry.locality,
            entry.required_providers,
            entry.optional_providers,
            _availability(entry, context, context.capabilities),
            context.capabilities.freshness,
            entry.authority,
            entry.effect_class,
            entry.retry_class,
            entry.projections,
            (
                CommandExample(
                    "minimal",
                    identity.mcp_tool,
                    identity.cli_argv,
                    json.dumps(example_payload, separators=(",", ":")),
                ),
            ),
            entry.lifecycle,
            entry.replacement_command_id,
            _access(entry, access),
        )


def _access(entry: ApplicationEntry, observation) -> CommandAccess:
    return (CommandAccess.ACTIVE if observation.allows(
        command_id=entry.command_id, required=entry.authority, effect=entry.effect_class,
    ) else CommandAccess.INACTIVE)


def _availability(entry: ApplicationEntry, context: InvocationContext, snapshot):
    if not context.dependency_tier.supports(entry.dependency_tier):
        return Availability.UNAVAILABLE
    unknown = False
    for provider_id in entry.required_providers:
        if context.providers.get(provider_id) is None:
            return Availability.UNAVAILABLE
        state = snapshot.availability_of(provider_id)
        if state is Availability.UNAVAILABLE:
            return Availability.UNAVAILABLE
        if state is Availability.UNKNOWN:
            unknown = True
    return Availability.UNKNOWN if unknown else Availability.AVAILABLE


def _ceiling_allows(context: InvocationContext, command_id: str) -> bool:
    """Hide catalogue entries excluded by an authenticated profile ceiling."""

    return context.authority.ceiling_allows(command_id)


def _entry(
    request_type: type,
    executor,
    *,
    authority: Authority = Authority.READER,
) -> ApplicationEntry:
    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=authority,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
    )


def build_application_catalogue(
    additional_entries: tuple[ApplicationEntry, ...] = (),
) -> ApplicationCatalogue:
    """Build the authoritative selected-Brain view as owners migrate in Phase 3."""

    owners = _FoundationOwners()
    entries = (
        _entry(CommandDescribeRequest, owners.command_describe),
        _entry(CommandListRequest, owners.command_list),
        _entry(
            InvocationReadRequest,
            owners.invocation_read,
            authority=Authority.CONTRIBUTOR,
        ),
        *additional_entries,
    )
    catalogue = ApplicationCatalogue(
        tuple(sorted(entries, key=lambda entry: entry.command_id))
    )
    owners.bind(catalogue)
    return catalogue


def _only(payload: Mapping[str, object], allowed: set[str]) -> None:
    reject_unexpected(payload, allowed)


def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_enum(payload: Mapping[str, object], name: str, enum_type):
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if enum_type is DependencyTier:
        try:
            return DependencyTier[value.upper()]
        except KeyError as exc:
            raise ValueError(f"unknown {name}: {value}") from exc
    return enum_type(value)


def _decode_list(payload: Mapping[str, object]) -> CommandListRequest:
    allowed = {
        "query",
        "domain",
        "owner",
        "availability",
        "authority",
        "dependency_tier",
        "locality",
        "effect_class",
        "retry_class",
        "projection",
        "cursor",
        "refresh",
        "page_size",
        "view",
    }
    _only(payload, allowed)
    page_size = payload.get("page_size", 25)
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise ValueError("page_size must be an integer")
    raw_cursor = payload.get("cursor")
    if raw_cursor is None:
        cursor = None
    elif isinstance(raw_cursor, Mapping):
        _only(raw_cursor, {"snapshot_token", "command_id"})
        snapshot_token = _optional_string(raw_cursor, "snapshot_token")
        command_id = _optional_string(raw_cursor, "command_id")
        if snapshot_token is None or command_id is None:
            raise ValueError("cursor requires snapshot_token and command_id")
        cursor = CatalogueCursor(snapshot_token, command_id)
    else:
        raise ValueError("cursor must be an object")
    refresh = payload.get("refresh", False)
    if not isinstance(refresh, bool):
        raise ValueError("refresh must be a boolean")
    return CommandListRequest(
        query=_optional_string(payload, "query"),
        domain=_optional_string(payload, "domain"),
        owner=_optional_enum(payload, "owner", CommandOwner),
        availability=_optional_enum(payload, "availability", Availability),
        authority=_optional_enum(payload, "authority", Authority),
        dependency_tier=_optional_enum(payload, "dependency_tier", DependencyTier),
        locality=_optional_enum(payload, "locality", Locality),
        effect_class=_optional_enum(payload, "effect_class", EffectClass),
        retry_class=_optional_enum(payload, "retry_class", RetryClass),
        projection=_optional_enum(payload, "projection", Projection),
        cursor=cursor,
        refresh=refresh,
        page_size=page_size,
        view=CommandListView(payload.get("view", "brief")),
    )


def _decode_describe(payload: Mapping[str, object]) -> CommandDescribeRequest:
    _only(payload, {"target_command_id"})
    target = _optional_string(payload, "target_command_id")
    if target is None:
        raise ValueError("target_command_id is required")
    return CommandDescribeRequest(target)


def _decode_invocation_read(payload: Mapping[str, object]) -> InvocationReadRequest:
    _only(payload, {"invocation_id"})
    invocation_id = _optional_string(payload, "invocation_id")
    if invocation_id is None:
        raise ValueError("invocation_id is required")
    return InvocationReadRequest(invocation_id)


def build_request_resolver(
    additional_entries: tuple[ResolverEntry, ...] = (),
) -> RequestResolver:
    entries = (
        ResolverEntry(CommandDescribeRequest, _decode_describe),
        ResolverEntry(CommandListRequest, _decode_list),
        ResolverEntry(InvocationReadRequest, _decode_invocation_read),
        *additional_entries,
    )
    return RequestResolver(tuple(sorted(entries, key=lambda entry: entry.command_id)))
