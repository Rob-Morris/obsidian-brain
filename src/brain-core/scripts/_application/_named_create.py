"""Shared execution mechanics for named configuration-resource creation."""

from __future__ import annotations

from dataclasses import dataclass
from ._mutation_support import (
    FrontmatterField,
    MutationContent,
    frontmatter_mapping,
    no_effect_error,
    resolve_mutation_content,
)
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandWarning, ErrorCode, Ok, WarningCode


@dataclass(frozen=True, slots=True)
class NamedResourceCreatePayload:
    resource: str
    name: str
    path: str
    staged_handle_consumed: bool


def execute_named_create_values(
    context: InvocationContext,
    request,
    *,
    resource: str,
    name: str,
    content: MutationContent,
    frontmatter: tuple[FrontmatterField, ...] | None,
):
    """Create one named document from values owned by a cohesive target union."""

    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    from _staging import finalise_staged_body
    from pathlib import Path
    import create

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )
    vault_root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(vault_root):
            router = require_fresh_compiled_router(vault_root)
            if resource == "template":
                rel_path = create.config_resource_rel_path(router, resource, name)
                target = Path(vault_root) / rel_path
                if target.exists() or target.is_symlink():
                    return no_effect_error(
                        type(request),
                        ErrorCode.CONFLICT,
                        f"Template '{name}' already exists at {rel_path}",
                        "name",
                    )
            body, staged_handle = resolve_mutation_content(
                vault_root,
                content,
                context=context,
            )
            plan = create.plan_named_resource_creation(
                vault_root,
                router,
                resource=resource,
                name=name,
                body=body,
                frontmatter=(
                    None
                    if frontmatter is None
                    else frontmatter_mapping(frontmatter)
                ),
            )
            from dataclasses import replace

            plan = replace(plan, exclusive=True)
            from .preparation import admit_owner
            from .preparation_creation import named_creation_binding

            admit_owner(context, request, named_creation_binding, plan=plan)
            result = create.apply_named_resource_creation(vault_root, plan)
            staging_warning = finalise_staged_body(vault_root, staged_handle)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ValueError as exc:
        return no_effect_error(
            type(request),
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
        committed_effects=(CommittedEffect(f"{resource}.created", payload.path),),
        warnings=warnings,
    )
