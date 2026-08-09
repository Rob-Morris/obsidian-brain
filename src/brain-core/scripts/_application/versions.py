"""Independent compatibility-version contracts for the command architecture."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .types import validate_command_id


class ChangeKind(str, Enum):
    COMPATIBLE_ADDITION = "compatible_addition"
    REQUEST_PROJECTION_BREAKING = "request_projection_breaking"
    COMMAND_CONTRACT_BREAKING = "command_contract_breaking"
    RESULT_SCHEMA_BREAKING = "result_schema_breaking"
    CATALOGUE_SCHEMA_BREAKING = "catalogue_schema_breaking"
    LAUNCHER_SCHEMA_BREAKING = "launcher_schema_breaking"
    PROXY_PROTOCOL_BREAKING = "proxy_protocol_breaking"
    FINGERPRINT_ONLY = "fingerprint_only"


@dataclass(frozen=True, slots=True)
class ContractChange:
    kind: ChangeKind
    command_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ChangeKind.COMMAND_CONTRACT_BREAKING:
            if self.command_id is None:
                raise ValueError("breaking command change requires a command_id")
            validate_command_id(self.command_id)
        elif self.command_id is not None:
            raise ValueError("only command-contract changes name a command_id")


@dataclass(frozen=True, slots=True)
class CommandVersion:
    command_id: str
    version: int

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.version < 1:
            raise ValueError("command version must be positive")


@dataclass(frozen=True, slots=True)
class InterfaceVersions:
    interface_epoch: int
    result_schema_version: int
    catalogue_schema_version: int
    launcher_schema_version: int
    proxy_protocol_version: int
    commands: tuple[CommandVersion, ...]

    def __post_init__(self) -> None:
        scalar_versions = (
            self.interface_epoch,
            self.result_schema_version,
            self.catalogue_schema_version,
            self.launcher_schema_version,
            self.proxy_protocol_version,
        )
        if any(version < 1 for version in scalar_versions):
            raise ValueError("interface versions must be positive")
        ids = [command.command_id for command in self.commands]
        if ids != sorted(ids):
            raise ValueError("command versions must be sorted by command_id")
        if len(ids) != len(set(ids)):
            raise ValueError("command versions must be unique")

    def command_version(self, command_id: str) -> int | None:
        match = next(
            (command for command in self.commands if command.command_id == command_id),
            None,
        )
        return None if match is None else match.version


def validate_transition(
    previous: InterfaceVersions,
    current: InterfaceVersions,
    changes: tuple[ContractChange, ...],
) -> None:
    """Require the independently owning version to move for each breaking change."""

    scalar_pairs = {
        "interface epoch": (previous.interface_epoch, current.interface_epoch),
        "result schema": (
            previous.result_schema_version,
            current.result_schema_version,
        ),
        "catalogue schema": (
            previous.catalogue_schema_version,
            current.catalogue_schema_version,
        ),
        "launcher schema": (
            previous.launcher_schema_version,
            current.launcher_schema_version,
        ),
        "proxy protocol": (
            previous.proxy_protocol_version,
            current.proxy_protocol_version,
        ),
    }
    for name, (old, new) in scalar_pairs.items():
        if new < old:
            raise ValueError(f"{name} cannot decrease")
    for old_command in previous.commands:
        new_version = current.command_version(old_command.command_id)
        if new_version is not None and new_version < old_command.version:
            raise ValueError(f"command version cannot decrease: {old_command.command_id}")

    required_scalar = {
        ChangeKind.REQUEST_PROJECTION_BREAKING: (
            "interface epoch",
            previous.interface_epoch,
            current.interface_epoch,
        ),
        ChangeKind.RESULT_SCHEMA_BREAKING: (
            "result schema",
            previous.result_schema_version,
            current.result_schema_version,
        ),
        ChangeKind.CATALOGUE_SCHEMA_BREAKING: (
            "catalogue schema",
            previous.catalogue_schema_version,
            current.catalogue_schema_version,
        ),
        ChangeKind.LAUNCHER_SCHEMA_BREAKING: (
            "launcher schema",
            previous.launcher_schema_version,
            current.launcher_schema_version,
        ),
        ChangeKind.PROXY_PROTOCOL_BREAKING: (
            "proxy protocol",
            previous.proxy_protocol_version,
            current.proxy_protocol_version,
        ),
    }
    for change in changes:
        scalar = required_scalar.get(change.kind)
        if scalar is not None:
            name, old, new = scalar
            if new <= old:
                raise ValueError(f"breaking {name} change requires a version increment")
        if change.kind is ChangeKind.COMMAND_CONTRACT_BREAKING:
            old = previous.command_version(change.command_id)
            new = current.command_version(change.command_id)
            if old is None or new is None or new <= old:
                raise ValueError(
                    f"breaking command change requires its version to increment: {change.command_id}"
                )


def replay_identity_compatible(
    *,
    accepted_epoch: int,
    accepted_command_id: str,
    accepted_command_version: int,
    accepted_mutation_class: str,
    replacement_epoch: int,
    replacement_command_id: str,
    replacement_command_version: int,
    replacement_mutation_class: str,
) -> bool:
    """Return the positive identity proof needed before later proxy replay.

    Catalogue fingerprints are intentionally absent: compatible additions
    change fingerprints and therefore cannot be a compatibility boundary.
    """

    return (
        accepted_epoch == replacement_epoch
        and accepted_command_id == replacement_command_id
        and accepted_command_version == replacement_command_version
        and accepted_mutation_class == replacement_mutation_class
    )
