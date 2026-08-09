"""Compose application and launcher discovery without merging authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from _launcher.discovery import (
    LauncherCommandDescription,
    LauncherCommandSummary,
    LauncherListPayload,
)


@dataclass(frozen=True, slots=True)
class ApplicationDiscoveryPage:
    """Selected-Brain discovery plus its authoritative catalogue provenance."""

    catalogue_schema: str
    catalogue_fingerprint: str
    entries: tuple[Mapping[str, object], ...]
    next_cursor: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if not self.catalogue_schema.startswith("brain.command-catalogue/"):
            raise ValueError("application discovery requires its catalogue schema")
        if not self.catalogue_fingerprint.startswith("sha256:"):
            raise ValueError("application discovery requires its catalogue fingerprint")


@dataclass(frozen=True, slots=True)
class ComposedCommandEntry:
    owner: str
    catalogue_schema: str
    catalogue_fingerprint: str
    command_id: str
    command_version: int
    summary: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        _validate_provenance(
            self.owner,
            self.catalogue_schema,
            self.catalogue_fingerprint,
        )
        if not self.command_id.strip() or self.command_version < 1:
            raise ValueError("composed discovery requires command identity")
        if not self.summary.strip():
            raise ValueError("composed discovery requires a summary")


@dataclass(frozen=True, slots=True)
class ComposedListPayload:
    entries: tuple[ComposedCommandEntry, ...]
    application_next_cursor: Mapping[str, object] | None
    launcher_next_cursor: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class ComposedDescription:
    owner: str
    catalogue_schema: str
    catalogue_fingerprint: str
    command_id: str
    command_version: int
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        _validate_provenance(
            self.owner,
            self.catalogue_schema,
            self.catalogue_fingerprint,
        )
        if not self.command_id.strip() or self.command_version < 1:
            raise ValueError("composed description requires command identity")


def compose_list(
    *,
    owner: str,
    application: ApplicationDiscoveryPage | None = None,
    launcher: LauncherListPayload | None = None,
) -> ComposedListPayload:
    """Compose a deterministic presentation view while retaining each source."""

    if owner not in {"application", "launcher", "all"}:
        raise ValueError("CLI command owner must be application, launcher or all")
    if owner in {"application", "all"} and application is None:
        raise ValueError("application discovery was requested but not supplied")
    if owner in {"launcher", "all"} and launcher is None:
        raise ValueError("launcher discovery was requested but not supplied")
    entries = []
    if application is not None and owner in {"application", "all"}:
        entries.extend(_application_entry(application, item) for item in application.entries)
    if launcher is not None and owner in {"launcher", "all"}:
        entries.extend(_launcher_entry(launcher, item) for item in launcher.entries)
    entries.sort(key=lambda item: (item.command_id, item.owner))
    identities = [(item.command_id, item.owner) for item in entries]
    if len(identities) != len(set(identities)):
        raise ValueError("composed discovery contains duplicate owner identities")
    command_ids = [item.command_id for item in entries]
    if len(command_ids) != len(set(command_ids)):
        raise ValueError("application and launcher command identities collide")
    launcher_cursor = None
    if launcher is not None and launcher.next_cursor is not None:
        launcher_cursor = asdict(launcher.next_cursor)
    return ComposedListPayload(
        tuple(entries),
        application.next_cursor if application is not None else None,
        launcher_cursor,
    )


def compose_application_description(
    *,
    catalogue_schema: str,
    catalogue_fingerprint: str,
    payload: Mapping[str, object],
) -> ComposedDescription:
    """Wrap an application description without altering its owned payload."""

    return ComposedDescription(
        "application",
        catalogue_schema,
        catalogue_fingerprint,
        _required_text(payload, "command_id"),
        _required_version(payload),
        payload,
    )


def compose_launcher_description(
    description: LauncherCommandDescription,
    *,
    catalogue_schema: str,
    catalogue_fingerprint: str,
) -> ComposedDescription:
    """Wrap a launcher description without reinterpreting its owner contract."""

    return ComposedDescription(
        "launcher",
        catalogue_schema,
        catalogue_fingerprint,
        description.command_id,
        description.command_version,
        asdict(description),
    )


def _application_entry(
    source: ApplicationDiscoveryPage,
    payload: Mapping[str, object],
) -> ComposedCommandEntry:
    return ComposedCommandEntry(
        "application",
        source.catalogue_schema,
        source.catalogue_fingerprint,
        _required_text(payload, "command_id"),
        _required_version(payload),
        _required_text(payload, "summary"),
        payload,
    )


def _launcher_entry(
    source: LauncherListPayload,
    summary: LauncherCommandSummary,
) -> ComposedCommandEntry:
    payload = asdict(summary)
    return ComposedCommandEntry(
        "launcher",
        source.schema,
        source.catalogue_fingerprint,
        summary.command_id,
        summary.command_version,
        summary.summary,
        payload,
    )


def _required_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"application discovery entry requires {field}")
    return value


def _required_version(payload: Mapping[str, object]) -> int:
    value = payload.get("command_version")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("application discovery entry requires command_version")
    return value


def _validate_provenance(owner: str, schema: str, fingerprint: str) -> None:
    expected_schema = {
        "application": "brain.command-catalogue/",
        "launcher": "brain.launcher-catalogue/",
    }.get(owner)
    if expected_schema is None:
        raise ValueError("composed discovery owner is invalid")
    if not schema.startswith(expected_schema):
        raise ValueError(f"{owner} discovery requires its catalogue schema")
    if not fingerprint.startswith("sha256:"):
        raise ValueError(f"{owner} discovery requires its catalogue fingerprint")
