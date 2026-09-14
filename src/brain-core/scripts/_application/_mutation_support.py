"""Shared catalogue and no-effect error mechanics for Brain mutations."""

from __future__ import annotations

from .types import InitialAuthorisationClass

from dataclasses import dataclass, field
from typing import Annotated, Literal, Mapping

from ._decoding import reject_unexpected
from .results import request_error
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
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
        reject_unexpected(value, {"source", "content"}, label="inline content fields")
        content = value.get("content")
        if not isinstance(content, str):
            raise ValueError("inline content must contain a string")
        return InlineContent(content)
    if source == "stage":
        reject_unexpected(value, {"source", "handle"}, label="staged content fields")
        handle = value.get("handle")
        if not isinstance(handle, str):
            raise ValueError("staged content must contain a string handle")
        return StagedContent(handle)
    raise ValueError("content source must be 'inline' or 'stage'")


def resolve_mutation_content(vault_root: str, content: MutationContent, *, context=None):
    if isinstance(content, InlineContent):
        return content.content, None
    if context is not None and context.admission is not None:
        source_key = "stage:" + content.handle
        frozen = context.admission.frozen_inputs or {}
        expected = frozen.get("pins", {}).get(source_key)
        pinned = context.admission.read_pinned(source_key)
        if pinned is not None:
            if expected is not None:
                from .preparation import content_digest

                if content_digest(pinned) != expected["sha256"]:
                    raise ValueError("prepared content pin changed; prepare the operation again")
            return pinned.decode("utf-8"), content.handle
        if expected is not None:
            raise ValueError("prepared content pin is unavailable; prepare the operation again")
    from _staging import read_staged_body

    return read_staged_body(vault_root, content.handle), content.handle


def decode_frontmatter(value: object) -> tuple[FrontmatterField, ...]:
    if not isinstance(value, Mapping):
        raise ValueError("frontmatter must be an object")
    if any(not isinstance(name, str) for name in value):
        raise ValueError("frontmatter field names must be strings")
    fields = []
    for name in sorted(value):
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


@dataclass(frozen=True, slots=True)
class FrontmatterCodec:
    """Project immutable frontmatter fields as one bounded JSON object."""

    nonempty: bool = False

    def schema(self) -> dict:
        scalar = ["string", "number", "boolean", "null"]
        result = {
            "type": "object",
            "propertyNames": {"pattern": r"\S"},
            "additionalProperties": {
                "type": [*scalar, "array"],
                "items": {"type": scalar},
            },
        }
        if self.nonempty:
            result["minProperties"] = 1
        return result

    def encode(self, value):
        return frontmatter_mapping(value)

    def decode(self, value):
        fields = decode_frontmatter(value)
        if self.nonempty and not fields:
            raise ValueError("frontmatter updates must be non-empty")
        return fields


FRONTMATTER_CODEC = FrontmatterCodec()
FRONTMATTER_PATCH_CODEC = FrontmatterCodec(nonempty=True)
Frontmatter = Annotated[tuple[FrontmatterField, ...], FRONTMATTER_CODEC]
FrontmatterPatch = Annotated[tuple[FrontmatterField, ...], FRONTMATTER_PATCH_CODEC]


no_effect_error = request_error


def contributor_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.CONTRIBUTOR)


def maintainer_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.MAINTAINER)


def operator_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.OPERATOR)


def administrator_mutation_entry(request_type, executor):
    return mutation_entry(request_type, executor, Authority.ADMINISTRATOR)


def mutation_entry(request_type, executor, authority: Authority):
    from .catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        initial_class=InitialAuthorisationClass.CONTENT,
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=authority,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
