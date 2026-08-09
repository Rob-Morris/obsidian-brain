"""Foundational discovery and invocation-outcome command owners."""

from __future__ import annotations

from typing import Mapping

from .catalogue import ApplicationCatalogue, ApplicationEntry
from .context import InvocationContext
from .receipts import OutcomeReference, ReceiptLookupState
from .requests import (
    CommandDescribeRequest,
    CommandDescriptionPayload,
    CommandListPayload,
    CommandListRequest,
    CommandRequest,
    InvocationReadPayload,
    InvocationReadRequest,
)
from .resolver import RequestResolver, ResolverEntry
from .results import CommandError, Error, ErrorCode, Ok, RequestErrorDetails
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


_ALL_APPLICATION_PROJECTIONS = tuple(
    ProjectionEligibility(projection, True)
    for projection in (
        Projection.MCP,
        Projection.CLI,
        Projection.SCRIPT,
        Projection.PYTHON,
    )
)


class _FoundationOwners:
    def __init__(self) -> None:
        self._catalogue: ApplicationCatalogue | None = None

    def bind(self, catalogue: ApplicationCatalogue) -> None:
        if self._catalogue is not None:
            raise RuntimeError("foundation command owners are already bound")
        self._catalogue = catalogue

    def command_list(self, _context: InvocationContext, request: CommandRequest):
        if type(request) is not CommandListRequest:
            raise TypeError("command.list owner received the wrong request type")
        catalogue = self._require_catalogue()
        entries = tuple(
            entry
            for entry in catalogue.entries
            if self._matches_list_filter(entry, request)
        )
        start = 0
        if request.cursor is not None:
            cursor_index = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if entry.command_id == request.cursor
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
        page = entries[start : start + request.page_size]
        has_more = start + len(page) < len(entries)
        next_cursor = page[-1].command_id if page and has_more else None
        return Ok(
            CommandListRequest.COMMAND_ID,
            CommandListRequest.COMMAND_VERSION,
            CommandListPayload(
                tuple(entry.command_id for entry in page),
                next_cursor,
            ),
        )

    def command_describe(self, _context: InvocationContext, request: CommandRequest):
        if type(request) is not CommandDescribeRequest:
            raise TypeError("command.describe owner received the wrong request type")
        entry = next(
            (
                candidate
                for candidate in self._require_catalogue().entries
                if candidate.command_id == request.target_command_id
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
            CommandDescriptionPayload(entry.command_id, entry.command_version),
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
    ) -> bool:
        if (
            request.query is not None
            and request.query.casefold() not in entry.command_id.casefold()
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
        if (
            request.projection is not None
            and request.projection not in entry.eligible_projections
        ):
            return False
        return True


def _entry(request_type: type, executor) -> ApplicationEntry:
    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=_ALL_APPLICATION_PROJECTIONS,
    )


def build_application_catalogue(
    additional_entries: tuple[ApplicationEntry, ...] = (),
) -> ApplicationCatalogue:
    """Build the authoritative selected-Brain view as owners migrate in Phase 3."""

    owners = _FoundationOwners()
    entries = (
        _entry(CommandDescribeRequest, owners.command_describe),
        _entry(CommandListRequest, owners.command_list),
        _entry(InvocationReadRequest, owners.invocation_read),
        *additional_entries,
    )
    catalogue = ApplicationCatalogue(
        tuple(sorted(entries, key=lambda entry: entry.command_id))
    )
    owners.bind(catalogue)
    return catalogue


def _only(payload: Mapping[str, object], allowed: set[str]) -> None:
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")


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
        "authority",
        "dependency_tier",
        "locality",
        "effect_class",
        "projection",
        "cursor",
        "page_size",
    }
    _only(payload, allowed)
    page_size = payload.get("page_size", 100)
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise ValueError("page_size must be an integer")
    return CommandListRequest(
        query=_optional_string(payload, "query"),
        domain=_optional_string(payload, "domain"),
        authority=_optional_enum(payload, "authority", Authority),
        dependency_tier=_optional_enum(payload, "dependency_tier", DependencyTier),
        locality=_optional_enum(payload, "locality", Locality),
        effect_class=_optional_enum(payload, "effect_class", EffectClass),
        projection=_optional_enum(payload, "projection", Projection),
        cursor=_optional_string(payload, "cursor"),
        page_size=page_size,
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
    return InvocationReadRequest(OutcomeReference(invocation_id))


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
