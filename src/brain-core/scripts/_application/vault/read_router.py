"""Typed ``vault.read-router`` command and internal executor."""

from __future__ import annotations

from .._decoding import decode_empty
from .._read_support import catalogue_entry as portable_reader_entry
from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import CommandError, Error, ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class RouterSource:
    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class RouterMetadataPayload:
    always_rules: tuple[str, ...]
    brain_core_version: str
    compiled_at: str
    source_hash: str
    sources: tuple[RouterSource, ...]
    artefact_index_sources: tuple[str, ...]
    artefact_index_source_count: int


@dataclass(frozen=True, slots=True)
class VaultReadRouterRequest:
    COMMAND_ID: ClassVar[str] = "vault.read-router"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RouterMetadataPayload


def execute(context: InvocationContext, _request: VaultReadRouterRequest):
    from _portable.router_views import router_meta_from_vault

    try:
        view = router_meta_from_vault(context.selected_brain.vault_root)
    except FileNotFoundError as exc:
        return Error(
            VaultReadRouterRequest.COMMAND_ID,
            VaultReadRouterRequest.COMMAND_VERSION,
            CommandError(ErrorCode.CONFLICT, str(exc)),
        )
    metadata = view["meta"]
    sources = metadata.get("sources") or {}
    if not isinstance(sources, dict):
        raise TypeError("router metadata sources must be a mapping")
    return Ok(
        VaultReadRouterRequest.COMMAND_ID,
        VaultReadRouterRequest.COMMAND_VERSION,
        RouterMetadataPayload(
            always_rules=tuple(view["always_rules"]),
            brain_core_version=metadata["brain_core_version"],
            compiled_at=metadata["compiled_at"],
            source_hash=metadata["source_hash"],
            sources=tuple(
                RouterSource(path, digest)
                for path, digest in sorted(sources.items())
            ),
            artefact_index_sources=tuple(metadata.get("artefact_index_sources") or ()),
            artefact_index_source_count=metadata.get("artefact_index_source_count", 0),
        ),
    )


def decode(payload: Mapping[str, object]) -> VaultReadRouterRequest:
    return decode_empty(payload, VaultReadRouterRequest)


def catalogue_entry():
    return portable_reader_entry(VaultReadRouterRequest, execute)
