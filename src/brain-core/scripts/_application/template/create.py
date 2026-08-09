"""Typed create-only ``template.create`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Mapping

from .._mutation_support import (
    MutationContent,
    contributor_mutation_entry,
    decode_mutation_content,
    no_effect_error,
    resolve_mutation_content,
)
from .._named_create import NamedResourceCreatePayload
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import CommandWarning, ErrorCode, Ok, WarningCode


@dataclass(frozen=True, slots=True)
class TemplateCreateRequest:
    COMMAND_ID: ClassVar[str] = "template.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = NamedResourceCreatePayload

    name: str
    content: MutationContent

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("template.create name must be a non-empty string")
        from .._mutation_support import InlineContent, StagedContent

        if not isinstance(self.content, (InlineContent, StagedContent)):
            raise ValueError("template.create content has an invalid variant")


def execute(context: InvocationContext, request: TemplateCreateRequest):
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
            TemplateCreateRequest,
            ErrorCode.INVALID_REQUEST,
            "template.create does not support dry-run",
        )
    vault_root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        return no_effect_error(
            TemplateCreateRequest,
            ErrorCode.CONFLICT,
            router["error"],
        )
    try:
        with vault_mutation_lock(vault_root):
            rel_path = create.config_resource_rel_path(
                router,
                "template",
                request.name,
            )
            target = Path(vault_root) / rel_path
            if target.exists() or target.is_symlink():
                return no_effect_error(
                    TemplateCreateRequest,
                    ErrorCode.CONFLICT,
                    f"Template '{request.name}' already exists at {rel_path}",
                    "name",
                )
            body, staged_handle = resolve_mutation_content(
                vault_root,
                request.content,
            )
            result = create.create_resource(
                vault_root,
                router,
                resource="template",
                name=request.name,
                body=body,
                frontmatter=None,
            )
            staging_warning = finalise_staged_body(vault_root, staged_handle)
    except MutationLockError as exc:
        return no_effect_error(
            TemplateCreateRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ValueError as exc:
        return no_effect_error(
            TemplateCreateRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
        )
    payload = NamedResourceCreatePayload(
        resource=result["resource"],
        name=result["name"],
        path=result["path"],
        staged_handle_consumed=staged_handle is not None and staging_warning is None,
    )
    warnings = (
        (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, staging_warning),)
        if staging_warning
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(CommittedEffect("template.created", payload.path),),
        warnings=warnings,
    )


def decode(payload: Mapping[str, object]) -> TemplateCreateRequest:
    unexpected = sorted(set(payload) - {"name", "content"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    name = payload.get("name")
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    return TemplateCreateRequest(
        name=name,
        content=decode_mutation_content(payload.get("content")),
    )


def catalogue_entry():
    return contributor_mutation_entry(TemplateCreateRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(TemplateCreateRequest, decode)
