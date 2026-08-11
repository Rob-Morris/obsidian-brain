"""Shared catalogue and no-effect error mechanics for Brain mutations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

from .results import CommandError, Error, ErrorCode, RequestErrorDetails
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


FrontmatterScalar = str | int | float | bool | None
FrontmatterValue = FrontmatterScalar | tuple[FrontmatterScalar, ...]


@dataclass(frozen=True, slots=True)
class InlineContent:
    content: str
    source: Literal["inline"] = field(default="inline", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("inline mutation content must be a string")


@dataclass(frozen=True, slots=True)
class StagedContent:
    handle: str
    source: Literal["stage"] = field(default="stage", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.handle, str) or not self.handle.strip():
            raise ValueError("staged mutation handle must be a non-empty string")


MutationContent = InlineContent | StagedContent


@dataclass(frozen=True, slots=True)
class FrontmatterField:
    name: str
    value: FrontmatterValue

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("frontmatter field name must be non-empty")
        values = self.value if isinstance(self.value, tuple) else (self.value,)
        if any(
            value is not None
            and not isinstance(value, (str, int, float, bool))
            for value in values
        ):
            raise ValueError(
                "frontmatter values must be scalars or flat scalar lists"
            )


def decode_mutation_content(value: object) -> MutationContent:
    if not isinstance(value, Mapping):
        raise ValueError("content must be an object with a source discriminator")
    source = value.get("source")
    if source == "inline":
        unexpected = sorted(set(value) - {"source", "content"})
        if unexpected:
            raise ValueError(
                f"unexpected inline content fields: {', '.join(unexpected)}"
            )
        content = value.get("content")
        if not isinstance(content, str):
            raise ValueError("inline content must contain a string")
        return InlineContent(content)
    if source == "stage":
        unexpected = sorted(set(value) - {"source", "handle"})
        if unexpected:
            raise ValueError(
                f"unexpected staged content fields: {', '.join(unexpected)}"
            )
        handle = value.get("handle")
        if not isinstance(handle, str):
            raise ValueError("staged content must contain a string handle")
        return StagedContent(handle)
    raise ValueError("content source must be 'inline' or 'stage'")


def resolve_mutation_content(vault_root: str, content: MutationContent):
    if isinstance(content, InlineContent):
        return content.content, None
    from _staging import read_staged_body

    return read_staged_body(vault_root, content.handle), content.handle


def decode_frontmatter(value: object) -> tuple[FrontmatterField, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("frontmatter must be an object")
    fields = []
    for name in sorted(value):
        if not isinstance(name, str):
            raise ValueError("frontmatter field names must be strings")
        item = value[name]
        if isinstance(item, list):
            item = tuple(item)
        elif isinstance(item, Mapping):
            raise ValueError("nested frontmatter objects are not supported")
        fields.append(FrontmatterField(name, item))
    return tuple(fields)


def frontmatter_mapping(fields: tuple[FrontmatterField, ...]) -> dict:
    return {
        item.name: list(item.value) if isinstance(item.value, tuple) else item.value
        for item in fields
    }


def no_effect_error(
    request_type,
    code: ErrorCode,
    message: str,
    field: str | None = None,
    *,
    retryable: bool = False,
) -> Error:
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(code, message, RequestErrorDetails(field, message)),
        retryable=retryable,
    )


def contributor_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.CONTRIBUTOR)


def maintainer_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.MAINTAINER)


def operator_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.OPERATOR)


def administrator_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.ADMINISTRATOR)


def mutation_entry(request_type, executor, authority: Authority):
    from .catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=authority,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
    )
