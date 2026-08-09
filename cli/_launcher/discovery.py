"""Authoritative launcher-owned discovery for the composed local CLI view."""

from __future__ import annotations

from dataclasses import dataclass
import json

from launcher_catalogue import (
    LAUNCHER_CATALOGUE,
    LauncherCatalogue,
    LauncherEntry,
    LauncherProjection,
)

from .context import ProviderBindings
from .contracts import ErrorCode, WarningCode, validate_command_id
from .owners import LAUNCHER_OWNERS, LauncherOwners
from .projection import minimal_request_payload, request_schema, result_schema


@dataclass(frozen=True, slots=True)
class LauncherCursor:
    catalogue_fingerprint: str
    command_id: str

    def __post_init__(self) -> None:
        if not self.catalogue_fingerprint.startswith("sha256:"):
            raise ValueError("launcher cursor requires a catalogue fingerprint")
        validate_command_id(self.command_id)


@dataclass(frozen=True, slots=True)
class LauncherCommandSummary:
    command_id: str
    command_version: int
    owner: str
    owner_ref: str
    summary: str
    entry_point: tuple[str, ...]
    projections: tuple[LauncherProjection, ...]
    dependency_tier: str
    locality: str
    authority: str
    effect_class: str
    retry_class: str
    required_providers: tuple[str, ...]
    availability: str
    availability_freshness: str
    missing_providers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LauncherListPayload:
    schema: str
    catalogue_fingerprint: str
    entries: tuple[LauncherCommandSummary, ...]
    next_cursor: LauncherCursor | None


@dataclass(frozen=True, slots=True)
class LauncherCommandDescription:
    command_id: str
    command_version: int
    owner: str
    owner_ref: str
    summary: str
    entry_point: tuple[str, ...]
    request_schema_json: str
    result_type: str
    result_schema_json: str
    result_variants: tuple[tuple[str, str], ...]
    error_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    dependency_tier: str
    locality: str
    required_providers: tuple[str, ...]
    availability: str
    availability_freshness: str
    missing_providers: tuple[str, ...]
    authority: str
    effect_class: str
    retry_class: str
    projections: tuple[LauncherProjection, ...]
    example_json: str


def list_commands(
    *,
    providers: ProviderBindings = ProviderBindings(),
    catalogue: LauncherCatalogue = LAUNCHER_CATALOGUE,
    query: str | None = None,
    domain: str | None = None,
    availability: str | None = None,
    authority: str | None = None,
    dependency_tier: str | None = None,
    locality: str | None = None,
    effect_class: str | None = None,
    retry_class: str | None = None,
    projection: str | None = None,
    cursor: LauncherCursor | None = None,
    page_size: int = 100,
) -> LauncherListPayload:
    """List launcher entries from their own manifest and known provider state."""

    _validate_filters(
        catalogue,
        query=query,
        domain=domain,
        availability=availability,
        authority=authority,
        dependency_tier=dependency_tier,
        locality=locality,
        effect_class=effect_class,
        retry_class=retry_class,
        projection=projection,
    )
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= 500
    ):
        raise ValueError("launcher list page_size must be between 1 and 500")
    if cursor is not None and cursor.catalogue_fingerprint != catalogue.fingerprint:
        raise ValueError("launcher cursor fingerprint does not match the current catalogue")
    entries = tuple(
        entry
        for entry in catalogue.entries
        if _matches(
            entry,
            providers,
            query=query,
            domain=domain,
            availability=availability,
            authority=authority,
            dependency_tier=dependency_tier,
            locality=locality,
            effect_class=effect_class,
            retry_class=retry_class,
            projection=projection,
        )
    )
    start = 0
    if cursor is not None:
        try:
            start = next(
                index + 1
                for index, entry in enumerate(entries)
                if entry.command_id == cursor.command_id
            )
        except StopIteration as exc:
            raise ValueError("launcher cursor is not present in the filtered view") from exc
    page = entries[start : start + page_size]
    has_more = start + len(page) < len(entries)
    next_cursor = (
        LauncherCursor(catalogue.fingerprint, page[-1].command_id)
        if page and has_more
        else None
    )
    return LauncherListPayload(
        catalogue.schema,
        catalogue.fingerprint,
        tuple(_summary(entry, providers) for entry in page),
        next_cursor,
    )


def describe_command(
    command_id: str,
    *,
    providers: ProviderBindings = ProviderBindings(),
    catalogue: LauncherCatalogue = LAUNCHER_CATALOGUE,
    owners: LauncherOwners = LAUNCHER_OWNERS,
) -> LauncherCommandDescription:
    """Describe one launcher request/result contract from its owning types."""

    entry = next((item for item in catalogue.entries if item.command_id == command_id), None)
    owner = next((item for item in owners.entries if item.command_id == command_id), None)
    if entry is None or owner is None:
        raise KeyError(f"launcher command is not installed: {command_id}")
    if (entry.owner_ref, entry.command_version) != (
        owner.owner_ref,
        owner.command_version,
    ):
        raise RuntimeError("launcher catalogue and owner identity do not agree")
    missing = _missing_providers(entry, providers)
    return LauncherCommandDescription(
        command_id=entry.command_id,
        command_version=entry.command_version,
        owner=entry.owner,
        owner_ref=entry.owner_ref,
        summary=entry.summary,
        entry_point=entry.entry_point,
        request_schema_json=_canonical_schema(request_schema(owner.request_type)),
        result_type=f"{owner.result_type.__module__}:{owner.result_type.__qualname__}",
        result_schema_json=_canonical_schema(result_schema(owner.result_type)),
        result_variants=(
            ("ok", "Typed result with any committed machine effects."),
            ("partial", "Known committed effects plus a typed error."),
            ("error", "No result and either no effects or an unknown outcome."),
        ),
        error_codes=tuple(item.value for item in ErrorCode),
        warning_codes=tuple(item.value for item in WarningCode),
        dependency_tier=entry.dependency_tier,
        locality=entry.locality,
        required_providers=entry.required_providers,
        availability="unavailable" if missing else "available",
        availability_freshness="fresh",
        missing_providers=missing,
        authority=entry.authority,
        effect_class=entry.effect_class,
        retry_class=entry.retry_class,
        projections=entry.projections,
        example_json=json.dumps(
            minimal_request_payload(owner.request_type),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _matches(entry: LauncherEntry, providers: ProviderBindings, **filters) -> bool:
    availability = "unavailable" if _missing_providers(entry, providers) else "available"
    query = filters["query"]
    if query is not None and query.casefold() not in (
        f"{entry.command_id} {entry.summary}".casefold()
    ):
        return False
    if filters["domain"] is not None and entry.command_id.split(".", 1)[0] != filters["domain"]:
        return False
    projection = filters["projection"]
    if projection is not None and not any(
        item.projection == projection and item.supported for item in entry.projections
    ):
        return False
    expected = {
        "availability": availability,
        "authority": entry.authority,
        "dependency_tier": entry.dependency_tier,
        "locality": entry.locality,
        "effect_class": entry.effect_class,
        "retry_class": entry.retry_class,
    }
    return all(filters[name] is None or filters[name] == value for name, value in expected.items())


def _validate_filters(
    catalogue: LauncherCatalogue,
    **filters: str | None,
) -> None:
    for name, value in filters.items():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"launcher list {name} must be a non-empty string")
    domain = filters["domain"]
    if domain is not None:
        try:
            validate_command_id(f"{domain}.list")
        except ValueError as exc:
            raise ValueError("launcher list domain must be a canonical noun") from exc
    allowed = {
        "availability": {"available", "unavailable"},
        "authority": {entry.authority for entry in catalogue.entries},
        "dependency_tier": {entry.dependency_tier for entry in catalogue.entries},
        "locality": {entry.locality for entry in catalogue.entries},
        "effect_class": {entry.effect_class for entry in catalogue.entries},
        "retry_class": {entry.retry_class for entry in catalogue.entries},
        "projection": {
            projection.projection
            for entry in catalogue.entries
            for projection in entry.projections
        },
    }
    for name, values in allowed.items():
        value = filters[name]
        if value is not None and value not in values:
            raise ValueError(f"launcher list {name} is invalid: {value}")


def _summary(entry: LauncherEntry, providers: ProviderBindings) -> LauncherCommandSummary:
    missing = _missing_providers(entry, providers)
    return LauncherCommandSummary(
        entry.command_id,
        entry.command_version,
        entry.owner,
        entry.owner_ref,
        entry.summary,
        entry.entry_point,
        entry.projections,
        entry.dependency_tier,
        entry.locality,
        entry.authority,
        entry.effect_class,
        entry.retry_class,
        entry.required_providers,
        "unavailable" if missing else "available",
        "fresh",
        missing,
    )


def _missing_providers(
    entry: LauncherEntry,
    providers: ProviderBindings,
) -> tuple[str, ...]:
    missing = []
    for provider_id in entry.required_providers:
        provider = providers.get(provider_id)
        if provider is None or not provider.available:
            missing.append(provider_id)
    return tuple(missing)


def _canonical_schema(schema: dict[str, object]) -> str:
    return json.dumps(
        schema,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
