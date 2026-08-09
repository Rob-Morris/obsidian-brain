"""Static selected-Brain command catalogue contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable

from .context import InvocationContext
from .requests import CommandRequest, command_identity
from .results import CommandResult
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    RetryClass,
    validate_command_id,
)


CATALOGUE_SCHEMA = "brain.command-catalogue/1"
Executor = Callable[[InvocationContext, CommandRequest], CommandResult]


@dataclass(frozen=True, slots=True)
class ApplicationEntry:
    request_type: type
    executor: Executor
    dependency_tier: DependencyTier
    locality: Locality
    required_providers: tuple[str, ...]
    optional_providers: tuple[str, ...]
    authority: Authority
    effect_class: EffectClass
    retry_class: RetryClass
    projections: tuple[Projection, ...]

    @property
    def command_id(self) -> str:
        return self.request_type.COMMAND_ID

    @property
    def command_version(self) -> int:
        return self.request_type.COMMAND_VERSION

    @property
    def result_type(self) -> type:
        return self.request_type.RESULT_TYPE

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1:
            raise ValueError("application entry command version must be positive")
        if self.locality is Locality.MACHINE_LOCAL:
            raise ValueError("machine-local commands belong to the launcher manifest")
        required = self.required_providers
        optional = self.optional_providers
        if any(not item.strip() for item in (*required, *optional)):
            raise ValueError("provider names must be non-empty")
        if len(required) != len(set(required)) or len(optional) != len(set(optional)):
            raise ValueError("provider names must be unique within each binding class")
        if set(required) & set(optional):
            raise ValueError("a provider cannot be both required and optional")
        if not self.projections or Projection.LAUNCHER in self.projections:
            raise ValueError("application projections must be non-empty and exclude launcher")
        if len(self.projections) != len(set(self.projections)):
            raise ValueError("application projections must be unique")


@dataclass(frozen=True, slots=True)
class ApplicationCatalogue:
    entries: tuple[ApplicationEntry, ...]
    schema: str = CATALOGUE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != CATALOGUE_SCHEMA:
            raise ValueError(f"unsupported application catalogue schema: {self.schema}")
        ids = [entry.command_id for entry in self.entries]
        request_types = [entry.request_type for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("application catalogue command identifiers must be unique")
        if len(request_types) != len(set(request_types)):
            raise ValueError("application catalogue request types must be unique")
        if ids != sorted(ids):
            raise ValueError("application catalogue entries must be sorted by command_id")

    def resolve(self, request: CommandRequest) -> ApplicationEntry:
        command_id, version, result_type = command_identity(request)
        entry = next(
            (item for item in self.entries if item.request_type is type(request)),
            None,
        )
        if entry is None:
            raise KeyError(f"request is not present in application catalogue: {command_id}")
        if (entry.command_id, entry.command_version, entry.result_type) != (
            command_id,
            version,
            result_type,
        ):
            raise RuntimeError(f"request/catalogue identity mismatch for {command_id}")
        return entry

    @property
    def fingerprint(self) -> str:
        payload = [
            {
                "command_id": entry.command_id,
                "command_version": entry.command_version,
                "result_type": entry.result_type.__name__,
                "dependency_tier": entry.dependency_tier.name.lower(),
                "locality": entry.locality.value,
                "required_providers": entry.required_providers,
                "optional_providers": entry.optional_providers,
                "authority": entry.authority.value,
                "effect_class": entry.effect_class.value,
                "retry_class": entry.retry_class.value,
                "projections": tuple(item.value for item in entry.projections),
            }
            for entry in self.entries
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
