"""Typed ``artefact.create`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._mutation_support import (
    FrontmatterField,
    InlineContent,
    MutationContent,
    StagedContent,
    contributor_mutation_entry,
    decode_frontmatter,
    decode_mutation_content,
    frontmatter_mapping,
    no_effect_error,
    resolve_mutation_content,
)
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import CommandWarning, ErrorCode, Ok, WarningCode
from .._wikilink_results import (
    WikilinkFinding,
    WikilinkFix,
    wikilink_result_values,
)


@dataclass(frozen=True, slots=True)
class TaggedArtefact:
    key: str
    path: str


@dataclass(frozen=True, slots=True)
class ParentContext:
    placed_under: str | None
    parent_path: str | None
    related: tuple[str, ...]
    tagged_artefacts: tuple[TaggedArtefact, ...]
    candidate_count: int | None
    hint: str


@dataclass(frozen=True, slots=True)
class ArtefactCreatePayload:
    path: str
    type: str
    title: str
    key: str | None
    parent: str | None
    parent_context: ParentContext | None
    wikilink_warnings: tuple[WikilinkFinding, ...]
    wikilink_fixes: tuple[WikilinkFix, ...]
    wikilink_substitutions: int
    staged_handle_consumed: bool


@dataclass(frozen=True, slots=True)
class ArtefactCreateRequest:
    COMMAND_ID: ClassVar[str] = "artefact.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactCreatePayload

    type: str
    title: str
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    parent: str | None = None
    key: str | None = None
    fix_links: bool = False

    def __post_init__(self) -> None:
        for field_name in ("type", "title"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"artefact.create {field_name} must be a non-empty string"
                )
        if self.content is not None and not isinstance(
            self.content, (InlineContent, StagedContent)
        ):
            raise ValueError("artefact.create content has an invalid variant")
        if not isinstance(self.frontmatter, tuple) or any(
            not isinstance(item, FrontmatterField) for item in self.frontmatter
        ):
            raise ValueError("artefact.create frontmatter must be typed fields")
        names = tuple(item.name for item in self.frontmatter)
        if len(names) != len(set(names)) or names != tuple(sorted(names)):
            raise ValueError(
                "artefact.create frontmatter fields must be unique and ordered"
            )
        for field_name in ("parent", "key"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"artefact.create {field_name} must be null or a non-empty string"
                )
        if not isinstance(self.fix_links, bool):
            raise ValueError("artefact.create fix_links must be a boolean")


def execute(context: InvocationContext, request: ArtefactCreateRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    from _staging import finalise_staged_body
    import create

    if context.dry_run:
        return no_effect_error(
            ArtefactCreateRequest,
            ErrorCode.INVALID_REQUEST,
            "artefact.create does not support dry-run",
        )
    vault_root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        return no_effect_error(
            ArtefactCreateRequest,
            ErrorCode.CONFLICT,
            router["error"],
        )
    try:
        with vault_mutation_lock(vault_root):
            if request.content is None:
                body, staged_handle = "", None
            else:
                body, staged_handle = resolve_mutation_content(
                    vault_root,
                    request.content,
                )
            result = create.create_resource(
                vault_root,
                router,
                resource="artefact",
                type_key=request.type,
                title=request.title,
                body=body,
                frontmatter_overrides=frontmatter_mapping(request.frontmatter),
                parent=request.parent,
                key=request.key,
                fix_links=request.fix_links,
            )
            staging_warning = finalise_staged_body(vault_root, staged_handle)
    except MutationLockError as exc:
        return no_effect_error(
            ArtefactCreateRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ValueError as exc:
        return no_effect_error(
            ArtefactCreateRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
        )

    payload = _payload(result, staged_handle, staging_warning)
    warnings = []
    if staging_warning:
        warnings.append(
            CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, staging_warning)
        )
    if payload.wikilink_warnings:
        warnings.append(
            CommandWarning(
                WarningCode.FOLLOW_UP_REQUIRED,
                f"Created artefact has {len(payload.wikilink_warnings)} unresolved wikilink finding(s).",
            )
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(CommittedEffect("artefact.created", payload.path),),
        warnings=tuple(warnings),
    )


def _payload(result, staged_handle, staging_warning) -> ArtefactCreatePayload:
    findings, fixes, substitutions = wikilink_result_values(result)
    return ArtefactCreatePayload(
        path=result["path"],
        type=result["type"],
        title=result["title"],
        key=result.get("key"),
        parent=result.get("parent"),
        parent_context=_parent_context(result.get("parent_context")),
        wikilink_warnings=findings,
        wikilink_fixes=fixes,
        wikilink_substitutions=substitutions,
        staged_handle_consumed=(
            staged_handle is not None and staging_warning is None
        ),
    )


def _parent_context(value) -> ParentContext | None:
    if value is None:
        return None
    return ParentContext(
        placed_under=value.get("placed_under"),
        parent_path=value.get("parent_path"),
        related=tuple(value.get("related") or ()),
        tagged_artefacts=tuple(
            TaggedArtefact(item["key"], item["path"])
            for item in value.get("tagged_artefacts") or ()
        ),
        candidate_count=value.get("candidate_count"),
        hint=value["hint"],
    )


def decode(payload: Mapping[str, object]) -> ArtefactCreateRequest:
    allowed = {
        "type",
        "title",
        "content",
        "frontmatter",
        "parent",
        "key",
        "fix_links",
    }
    reject_unexpected(payload, allowed)
    type_key = payload.get("type")
    title = payload.get("title")
    if not isinstance(type_key, str) or not isinstance(title, str):
        raise ValueError("type and title must be strings")
    raw_content = payload.get("content")
    parent = payload.get("parent")
    key = payload.get("key")
    fix_links = payload.get("fix_links", False)
    if parent is not None and not isinstance(parent, str):
        raise ValueError("parent must be null or a string")
    if key is not None and not isinstance(key, str):
        raise ValueError("key must be null or a string")
    if not isinstance(fix_links, bool):
        raise ValueError("fix_links must be a boolean")
    return ArtefactCreateRequest(
        type=type_key,
        title=title,
        content=(
            None if raw_content is None else decode_mutation_content(raw_content)
        ),
        frontmatter=decode_frontmatter(payload.get("frontmatter")),
        parent=parent,
        key=key,
        fix_links=fix_links,
    )


def catalogue_entry():
    return contributor_mutation_entry(ArtefactCreateRequest, execute)
